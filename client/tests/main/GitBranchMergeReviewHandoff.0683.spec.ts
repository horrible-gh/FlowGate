/**
 * flowgate.default.0683 T0004 §1/§2 — a branch merge whose conflicts are resolved reaches
 * a person's review, and the review can give the merge up.
 *
 * The 0683 incident: branch merge #206 (main → v0.2) went conflict → AI resolve →
 * `resolved_pending_review`, but the Branch Manager card kept saying "AI is resolving" and
 * nothing opened the review. The attempt held the v0.2 workspace for hours and every v0.2
 * group approval was refused. These cases pin every road into that review:
 *
 *   1. merge → 202 conflict → (AI resolves) → the review dialog opens by itself
 *   2. polling inside the host: ai_resolving → resolved_pending_review → review + notice
 *   3. a manual resolve submit answering resolved_pending_review → review
 *   4. coming back later: an open resolved_pending_review row opens the review
 *   5. a 202 that is already resolved_pending_review → review at once
 *   6. the review of a branch merge has [병합 중단] → POST …/abort → closed
 *   7. a group/TR review (no `abortable`) keeps its approve/reject-only footer
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

import GitBranchManager from '@main/components/GitBranchManager.vue'
import GitBranchMergeConflictHost from '@main/components/GitBranchMergeConflictHost.vue'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'

const { getRequest, postRequest, putRequest, deleteRequest, showToast, confirmMock } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest: vi.fn(),
  deleteRequest: vi.fn(),
  showToast: vi.fn(),
  confirmMock: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  getRequest,
  postRequest,
  putRequest,
  deleteRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

vi.mock('@main/composables/useDialogStack', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  confirm: (...args: unknown[]) => confirmMock(...args),
}))

const PROJECT = 'flowgate'
const BASE = `/api/v1/projects/${PROJECT}/git/merge/42`
const CONFLICT = ['<<<<<<< HEAD', 'develop line', '=======', 'feature line', '>>>>>>> feature', ''].join('\n')

function attempt(state: string, ai: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) {
  return {
    ok: true,
    result: {
      merge_id: 42, state, source_branch: 'feature', target_branch: 'develop', push: false,
      file_count: 1, resolved_count: state === 'resolved_pending_review' ? 1 : 0,
      ai: { status: 'running', run_id: 'run-1', provider_id: 'Claude', error: null, ...ai },
      ...extra,
    },
  }
}

const CATALOG = {
  base_branch: 'main',
  default_merge_target: null,
  branches: [
    { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
    { name: 'develop', kind: 'local', can_delete: true, can_be_create_source: true },
    { name: 'feature', kind: 'local', can_delete: true, can_be_create_source: true },
  ],
  open_branch_merges: [] as unknown[],
}

const hostStub = {
  name: 'GitBranchMergeConflictHost',
  props: ['projectId', 'mergeId'],
  emits: ['close', 'changed'],
  template: '<div data-test="host-stub" />',
}

const reviewStub = {
  name: 'GitMergeReviewDialog',
  props: ['groupId', 'mergeId', 'mergeApiBase', 'branch', 'baseBranch', 'providers',
    'selectedProvider', 'providerLoading', 'providerErrored', 'abortable', 'abortBusy'],
  emits: ['close', 'resolved', 'abort', 'update:provider'],
  template: '<div data-test="review-stub" />',
}

const resolverStub = {
  name: 'GitConflictResolverDialog',
  props: ['files', 'branch', 'baseBranch', 'busy', 'loadStatus', 'errorMessage', 'providers',
    'selectedProvider', 'providerLoading', 'providerErrored', 'aiRunNotice', 'aiRunPending',
    'hideAutoAuthority', 'hideCopyMention'],
  template: '<div data-test="resolver-stub" />',
}

function mountManager() {
  return mount(GitBranchManager, {
    props: { projectId: PROJECT },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitBranchMergeConflictHost: hostStub } },
  })
}

function mountHost() {
  return mount(GitBranchMergeConflictHost, {
    props: { projectId: PROJECT, mergeId: 42 },
    global: {
      plugins: [i18n],
      stubs: { AppIcon: true, GitConflictResolverDialog: resolverStub, GitMergeReviewDialog: reviewStub },
    },
  })
}

function toastTexts(): string[] {
  return showToast.mock.calls.map(([text]) => String(text))
}

beforeEach(() => {
  setActivePinia(createPinia())
  ;(Element.prototype as any).scrollTo = vi.fn()
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
  deleteRequest.mockReset()
  showToast.mockReset()
  confirmMock.mockReset()
  confirmMock.mockResolvedValue(true)
})

afterEach(() => {
  resetDialogSystem()
  vi.useRealTimers()
})

describe('Branch Manager → review hand-off (0683 T0004 §1)', () => {
  it('1. merge → 202 conflict → AI resolves while the card is up → the review opens by itself', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    let attemptReads = 0
    getRequest.mockImplementation((url: string) => {
      if (url.endsWith('/git/branches')) return Promise.resolve({ data: CATALOG })
      if (url === BASE) {
        attemptReads += 1
        return Promise.resolve({ data: attemptReads === 1 ? attempt('ai_resolving') : attempt('resolved_pending_review') })
      }
      return Promise.resolve({ data: {} })
    })
    postRequest.mockResolvedValue({
      data: { status: 'conflict', merge_id: 42, conflict_files: ['same.txt'], ai: { status: 'running' } },
    })
    const wrapper = mountManager()
    await flushPromises()
    ;(wrapper.vm as any).mergeSource = 'feature'
    ;(wrapper.vm as any).mergeTarget = 'develop'
    await (wrapper.vm as any).confirmMerge()
    await flushPromises()

    expect(wrapper.find('[data-test="merge-conflict-result"]').exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(false)

    await vi.advanceTimersByTimeAsync(3100)        // still resolving
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(false)

    await vi.advanceTimersByTimeAsync(3100)        // resolved_pending_review
    await flushPromises()
    const host = wrapper.findComponent({ name: 'GitBranchMergeConflictHost' })
    expect(host.exists()).toBe(true)
    expect(host.props('mergeId')).toBe(42)
    expect(host.props('projectId')).toBe(PROJECT)
    // the card no longer claims the AI is still working, and it does not say "merged"
    expect(wrapper.get('[data-test="merge-conflict-ai"]').text())
      .toBe(i18n.global.t('main.git_branch_manager.merge_conflict_ai.review'))
    expect(wrapper.get('[data-test="merge-conflict-open"]').text())
      .toBe(i18n.global.t('main.git_branch_manager.merge_review_open'))
    expect(toastTexts()).toContain(i18n.global.t('main.git_branch_manager.merge_review_ready_toast'))
    wrapper.unmount()
  })

  it('5. a 202 that is already resolved_pending_review opens the review at once', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.endsWith('/git/branches')) return Promise.resolve({ data: CATALOG })
      return Promise.resolve({ data: {} })
    })
    postRequest.mockResolvedValue({
      data: { status: 'resolved_pending_review', merge_id: 42, conflict_files: ['same.txt'] },
    })
    const wrapper = mountManager()
    await flushPromises()
    ;(wrapper.vm as any).mergeSource = 'feature'
    ;(wrapper.vm as any).mergeTarget = 'develop'
    await (wrapper.vm as any).confirmMerge()
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).props('mergeId')).toBe(42)
    wrapper.unmount()
  })

  it('a person who closes the review is not pulled back in, and an approved merge clears the card', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.endsWith('/git/branches')) return Promise.resolve({ data: CATALOG })
      if (url === BASE) return Promise.resolve({ data: attempt('completed', { status: 'finished' }, { pushed: false }) })
      return Promise.resolve({ data: {} })
    })
    postRequest.mockResolvedValue({
      data: { status: 'resolved_pending_review', merge_id: 42, conflict_files: ['same.txt'] },
    })
    const wrapper = mountManager()
    await flushPromises()
    ;(wrapper.vm as any).mergeSource = 'feature'
    ;(wrapper.vm as any).mergeTarget = 'develop'
    await (wrapper.vm as any).confirmMerge()
    await flushPromises()
    wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).vm.$emit('close')
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(false)
    expect(wrapper.find('[data-test="merge-conflict-result"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="merge-result-pushed"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('4. coming back later: an open resolved_pending_review row says [검토 열기] and opens it', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.endsWith('/git/branches')) {
        return Promise.resolve({ data: { ...CATALOG, open_branch_merges: [{
          merge_id: 42, source_branch: 'main', target_branch: 'v0.2',
          state: 'resolved_pending_review', file_count: 16, resolved_count: 16,
        }] } })
      }
      return Promise.resolve({ data: {} })
    })
    const wrapper = mountManager()
    await flushPromises()
    const row = wrapper.get('[data-test="open-merge-row"]')
    expect(row.get('[data-test="open-merge-state"]').text())
      .toBe(i18n.global.t('main.git_branch_manager.attempt_state.resolved_pending_review'))
    const open = row.get('[data-test="open-merge-resolve"]')
    expect(open.text()).toBe(i18n.global.t('main.git_branch_manager.merge_review_open'))
    await open.trigger('click')
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).props('mergeId')).toBe(42)
    wrapper.unmount()
  })
})

describe('GitBranchMergeConflictHost → review (0683 T0004 §1/§2)', () => {
  it('2. polling: ai_resolving → resolved_pending_review switches to the review and says so', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    let reads = 0
    getRequest.mockImplementation((url: string) => {
      if (url === BASE) {
        reads += 1
        return Promise.resolve({ data: reads === 1 ? attempt('ai_resolving') : attempt('resolved_pending_review') })
      }
      return Promise.resolve({ data: { ok: true, files: [{ path: 'same.txt', content: CONFLICT, conflict_count: 1 }] } })
    })
    const wrapper = mountHost()
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitConflictResolverDialog' }).exists()).toBe(true)
    await vi.advanceTimersByTimeAsync(3100)
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitMergeReviewDialog' }).exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'GitConflictResolverDialog' }).exists()).toBe(false)
    expect(toastTexts()).toContain(i18n.global.t('main.git_review.resolved_pending_opened'))
    wrapper.unmount()
  })

  it('3. a manual resolve submit answering resolved_pending_review lands on the review', async () => {
    let resolved = false
    getRequest.mockImplementation((url: string) => {
      if (url === BASE) return Promise.resolve({ data: resolved ? attempt('resolved_pending_review') : attempt('conflict', { status: 'not_started' }) })
      return Promise.resolve({ data: { ok: true, files: [{ path: 'same.txt', content: 'merged\n', conflict_count: 0 }] } })
    })
    postRequest.mockImplementation(async () => {
      resolved = true
      return { data: { ok: true, result: { status: 'resolved_pending_review', remaining_conflicts: [] } } }
    })
    const wrapper = mountHost()
    await flushPromises()
    wrapper.findComponent({ name: 'GitConflictResolverDialog' }).vm.$emit('submit', false)
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`${BASE}/resolve`, expect.objectContaining({ complete: true }))
    expect(wrapper.findComponent({ name: 'GitMergeReviewDialog' }).exists()).toBe(true)
    wrapper.unmount()
  })

  it('6. the branch merge review offers abort: confirm → POST …/abort → closed', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url === BASE) return Promise.resolve({ data: attempt('resolved_pending_review', { status: 'finished' }) })
      return Promise.resolve({ data: {} })
    })
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'aborted', workspace_cleaned: true } } })
    const wrapper = mountHost()
    await flushPromises()
    const review = wrapper.findComponent({ name: 'GitMergeReviewDialog' })
    expect(review.props('abortable')).toBe(true)
    review.vm.$emit('abort')
    await flushPromises()
    expect(confirmMock).toHaveBeenCalledTimes(1)
    expect(postRequest).toHaveBeenCalledWith(`${BASE}/abort`, {})
    expect(wrapper.emitted('changed')).toBeTruthy()
    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })

  it('6b. a declined confirm aborts nothing', async () => {
    confirmMock.mockResolvedValue(false)
    getRequest.mockImplementation((url: string) => {
      if (url === BASE) return Promise.resolve({ data: attempt('resolved_pending_review', { status: 'finished' }) })
      return Promise.resolve({ data: {} })
    })
    const wrapper = mountHost()
    await flushPromises()
    wrapper.findComponent({ name: 'GitMergeReviewDialog' }).vm.$emit('abort')
    await flushPromises()
    expect(postRequest).not.toHaveBeenCalled()
    expect(wrapper.emitted('close')).toBeFalsy()
    wrapper.unmount()
  })
})

describe('GitMergeReviewDialog abort action (0683 T0004 §2)', () => {
  function reviewPayload(state = 'resolved_pending_review') {
    return {
      ok: true,
      result: {
        merge_id: 42, review_state: state, review_fingerprint: 'fp-1', instruction_generation: 0,
        base_head: 'b', merge_head: 'm', snapshot_tree: 't', changes: [], conflict_origins: [],
        conversation: [], held_test_operations: [], resolver_provider: 'Claude', auto_authority: false,
        reconciliation_kind: null, last_error: null, can_approve: true, can_reject: true, can_send: true,
      },
    }
  }

  function mountDialog(props: Record<string, unknown>, state = 'resolved_pending_review') {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/review')) return Promise.resolve({ data: reviewPayload(state) })
      return Promise.resolve({ data: {} })
    })
    return mount(GitMergeReviewDialog, {
      props: { groupId: '', mergeId: 42, mergeApiBase: BASE, providers: [], selectedProvider: 'p1', ...props },
      global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
    })
  }

  it('a branch merge review (abortable) shows [병합 중단] and emits abort', async () => {
    const wrapper = mountDialog({ abortable: true })
    await flushPromises()
    const abort = wrapper.find('[data-dialog-action-id="abort"]')
    expect(abort.exists()).toBe(true)
    expect(abort.text()).toContain(i18n.global.t('main.git_review.abort'))
    await abort.trigger('click')
    expect(wrapper.emitted('abort')).toHaveLength(1)
    expect(postRequest).not.toHaveBeenCalled()      // the host owns the POST
    wrapper.unmount()
  })

  it('7. a group / TR review keeps its approve/reject-only footer', async () => {
    const wrapper = mountDialog({})
    await flushPromises()
    expect(wrapper.find('[data-dialog-action-id="abort"]').exists()).toBe(false)
    expect(wrapper.find('[data-dialog-action-id="approve"]').exists()).toBe(true)
    expect(wrapper.find('[data-dialog-action-id="reject"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('an attempt already applying cannot be aborted from the review', async () => {
    const wrapper = mountDialog({ abortable: true }, 'applying')
    await flushPromises()
    expect(wrapper.find('[data-dialog-action-id="abort"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })
})
