// 0530 T0004: persisted ordinary branch merges remain reviewable.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

import GitBranchManager from '@main/components/GitBranchManager.vue'
import GitBranchMergeConflictHost from '@main/components/GitBranchMergeConflictHost.vue'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'
import { useAiProviderStore } from '@main/stores/aiProvider'

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


function recovered(state: string, id = 42) {
  return { merge_id: id, state, source_branch: 'feature', target_branch: 'develop',
    file_count: 1, resolved_count: state === 'resolved_pending_review' ? 1 : 0 }
}

describe('0530 persisted branch merge review recovery', () => {
  it('recovers an already pending review after refresh without a result card', async () => {
    getRequest.mockResolvedValue({ data: { ...CATALOG, open_branch_merges: [recovered('resolved_pending_review')] } })
    const wrapper = mountManager()
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).props('mergeId')).toBe(42)
    expect(wrapper.find('[data-test="merge-conflict-result"]').exists()).toBe(false)
    expect(postRequest).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('tracks an existing AI attempt after re-entry and recovers its late review', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    let state = 'ai_resolving'
    getRequest.mockImplementation(() => Promise.resolve({ data: { ...CATALOG, open_branch_merges: [recovered(state)] } }))
    const wrapper = mountManager()
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(false)
    state = 'resolved_pending_review'
    await vi.advanceTimersByTimeAsync(3100)
    await flushPromises()
    expect(wrapper.get('[data-test="open-merge-state"]').text()).toBe(i18n.global.t('main.git_branch_manager.attempt_state.resolved_pending_review'))
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).props('mergeId')).toBe(42)
    expect(postRequest).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('closing the resolver during AI still permits one hand-off when resolution lands', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    let state = 'ai_resolving'
    getRequest.mockImplementation(() => Promise.resolve({ data: { ...CATALOG, open_branch_merges: [recovered(state)] } }))
    const wrapper = mountManager()
    await flushPromises()
    await wrapper.get('[data-test="open-merge-resolve"]').trigger('click')
    wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).vm.$emit('close')
    await flushPromises()
    state = 'resolved_pending_review'
    await vi.advanceTimersByTimeAsync(3100)
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(true)
    wrapper.unmount()
  })

  it('keeps a deliberately closed pending review closed through repeated polls and manual reopening', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    getRequest.mockResolvedValue({ data: { ...CATALOG, open_branch_merges: [recovered('resolved_pending_review')] } })
    const wrapper = mountManager()
    await flushPromises()
    wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).vm.$emit('close')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(9100)
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(false)
    await wrapper.get('[data-test="open-merge-resolve"]').trigger('click')
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(true)
    wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).vm.$emit('close')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(3100)
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(false)
    wrapper.unmount()
  })

  it('recovers pending review when returning from a hidden tab without consuming its hand-off', async () => {
    getRequest.mockResolvedValue({ data: { ...CATALOG, open_branch_merges: [recovered('resolved_pending_review')] } })
    const wrapper = mount(GitBranchManager, { props: { projectId: PROJECT, view: 'hidden' },
      global: { plugins: [i18n], stubs: { AppIcon: true, GitBranchMergeConflictHost: hostStub } } })
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(false)
    await wrapper.setProps({ view: 'manage' })
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).props('mergeId')).toBe(42)
    wrapper.unmount()
  })

  it('keeps other pending attempts reachable when the first review was closed', async () => {
    getRequest.mockResolvedValue({ data: { ...CATALOG, open_branch_merges: [recovered('resolved_pending_review'), recovered('resolved_pending_review', 43)] } })
    const wrapper = mountManager()
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).props('mergeId')).toBe(42)
    wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).vm.$emit('close')
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).props('mergeId')).toBe(43)
    expect(wrapper.findAll('[data-test="open-merge-resolve"]')).toHaveLength(2)
    wrapper.unmount()
  })

  it('retries catalog errors and never fabricates a review from an incomplete attempt', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    let reads = 0
    getRequest.mockImplementation(() => {
      reads += 1
      if (reads === 2) return Promise.reject({ response: { data: { error: { message: 'catalog temporarily unavailable' } } } })
      return Promise.resolve({ data: { ...CATALOG, open_branch_merges: [recovered(reads >= 3 ? 'resolved_pending_review' : 'conflict_remaining')] } })
    })
    const wrapper = mountManager()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(3100)
    await flushPromises()
    expect(wrapper.text()).toContain('catalog temporarily unavailable')
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(3100)
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(true)
    wrapper.unmount()
  })

  it('ignores a late catalog response after switching projects', async () => {
    let finishOld!: (value: any) => void
    getRequest.mockImplementation((url: string) => url.includes('/projects/flowgate/')
      ? new Promise(resolve => { finishOld = resolve }) : Promise.resolve({ data: CATALOG }))
    const wrapper = mountManager()
    await wrapper.setProps({ projectId: 'other-project' })
    await flushPromises()
    finishOld({ data: { ...CATALOG, open_branch_merges: [recovered('resolved_pending_review')] } })
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitBranchMergeConflictHost' }).exists()).toBe(false)
    expect(wrapper.find('[data-test="open-merge-row"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('does not open dialogs or leave timers when an in-flight catalog returns after unmount', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    let finish!: (value: any) => void
    getRequest.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const wrapper = mountManager()
    wrapper.unmount()
    finish({ data: { ...CATALOG, open_branch_merges: [recovered('resolved_pending_review')] } })
    await flushPromises()
    expect(showToast).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('0530 conflict Host recovery and errors', () => {
  it('a recovered completed attempt closes without leaving a resolver or review open', async () => {
    getRequest.mockResolvedValue({ data: attempt('completed', { status: 'finished' }) })
    const wrapper = mountHost()
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitConflictResolverDialog' }).exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'GitMergeReviewDialog' }).exists()).toBe(false)
    expect(wrapper.emitted('close')).toBeTruthy()
    expect(postRequest).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('shows a failed resolver reason without fabricating review state', async () => {
    getRequest.mockImplementation((url: string) => Promise.resolve({ data: url === BASE
      ? attempt('conflict_remaining', { status: 'failed', error: { message: 'resolution failed: other.txt remains' } })
      : { files: [] } }))
    const wrapper = mountHost()
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitConflictResolverDialog' }).props('errorMessage')).toContain('other.txt')
    expect(wrapper.findComponent({ name: 'GitMergeReviewDialog' }).exists()).toBe(false)
    expect(postRequest).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('detects late resolution from conflict_remaining while the Host is still open', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    let state = 'conflict_remaining'
    getRequest.mockImplementation((url: string) => Promise.resolve({ data: url === BASE ? attempt(state) : { files: [] } }))
    const wrapper = mountHost()
    await flushPromises()
    state = 'resolved_pending_review'
    await vi.advanceTimersByTimeAsync(3100)
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitMergeReviewDialog' }).exists()).toBe(true)
    expect(postRequest).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('shows the first read error and retries into the persisted review', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    let reads = 0
    getRequest.mockImplementation((url: string) => {
      if (url !== BASE) return Promise.resolve({ data: {} })
      reads += 1
      if (reads === 1) return Promise.reject({ response: { data: { error: { code: 'read_failed', message: 'attempt temporarily unavailable' } } } })
      return Promise.resolve({ data: attempt('resolved_pending_review') })
    })
    const wrapper = mountHost()
    await flushPromises()
    const resolver = wrapper.findComponent({ name: 'GitConflictResolverDialog' })
    expect(resolver.props('loadStatus')).toBe('error')
    expect(resolver.props('errorMessage')).toBeTruthy()
    await vi.advanceTimersByTimeAsync(3100)
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitMergeReviewDialog' }).exists()).toBe(true)
    wrapper.unmount()
  })

  it('reports remaining conflicts without opening a review or approving', async () => {
    getRequest.mockImplementation((url: string) => Promise.resolve({ data: url === BASE
      ? attempt('conflict_remaining', { status: 'finished' }) : { files: [{ path: 'same.txt', content: 'partial\n', conflict_count: 0 }] } }))
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'conflict', remaining_conflicts: ['other.txt'] } } })
    const wrapper = mountHost()
    await flushPromises()
    wrapper.findComponent({ name: 'GitConflictResolverDialog' }).vm.$emit('submit')
    await flushPromises()
    expect(wrapper.findComponent({ name: 'GitMergeReviewDialog' }).exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'GitConflictResolverDialog' }).props('errorMessage')).toContain('other.txt')
    expect(postRequest).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})

function reviewPayload(fingerprint = 'fp-0530') {
  return { ok: true, result: { merge_id: 42, review_state: 'resolved_pending_review', review_fingerprint: fingerprint,
    instruction_generation: 0, base_head: 'b', merge_head: 'm', snapshot_tree: 't', changes: [], conflict_origins: [],
    conversation: [], held_test_operations: [], resolver_provider: 'Claude', auto_authority: false,
    reconciliation_kind: null, last_error: null, can_approve: true, can_reject: true, can_send: true } }
}

describe('0530 actual review dialog to project approval route', () => {
  function realHost() {
    return mount(GitBranchMergeConflictHost, { props: { projectId: PROJECT, mergeId: 42 }, global: {
      plugins: [i18n], stubs: { AppIcon: true, teleport: true, GitConflictResolverDialog: resolverStub },
    } })
  }

  it('requires the explicit approve click and closes only after the API confirms the merge', async () => {
    let state = 'resolved_pending_review'
    getRequest.mockImplementation((url: string) => Promise.resolve({ data: url === BASE ? attempt(state)
      : url === `${BASE}/review` ? reviewPayload() : {} }))
    postRequest.mockImplementation(async (url: string) => {
      expect(url).toBe(`${BASE}/approve`)
      state = 'completed'
      return { data: { ok: true, result: { status: 'merged', pushed: false } } }
    })
    const wrapper = realHost()
    await flushPromises()
    expect(postRequest).not.toHaveBeenCalled()
    await wrapper.get('[data-dialog-action-id="approve"]').trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`${BASE}/approve`, {
      attempt_id: expect.stringMatching(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i),
      review_fingerprint: 'fp-0530',
    })
    expect(wrapper.emitted('changed')).toBeTruthy()
    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })

  it('keeps stale approval visible and loads the new fingerprint for the next explicit approval', async () => {
    let fingerprint = 'fp-0530'
    getRequest.mockImplementation((url: string) => Promise.resolve({ data: url === BASE ? attempt('resolved_pending_review')
      : url === `${BASE}/review` ? reviewPayload(fingerprint) : {} }))
    postRequest.mockImplementation(async () => {
      fingerprint = 'fp-new'
      throw { response: { data: { error: { code: 'stale_review', message: 'candidate changed' } } } }
    })
    const wrapper = realHost()
    await flushPromises()
    await wrapper.get('[data-dialog-action-id="approve"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('close')).toBeFalsy()
    expect(wrapper.findComponent(GitMergeReviewDialog).exists()).toBe(true)
    expect(wrapper.find('[data-test="gmr-approve-outcome"]').exists()).toBe(true)
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 're_review' } } })
    await wrapper.get('[data-dialog-action-id="approve"]').trigger('click')
    await flushPromises()
    expect(postRequest.mock.calls[1][1].review_fingerprint).toBe('fp-new')
    expect(wrapper.emitted('close')).toBeFalsy()
    wrapper.unmount()
  })

  it('rejects through the project route and recovers the subsequent new review', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    let state = 'resolved_pending_review'
    getRequest.mockImplementation((url: string) => Promise.resolve({ data: url === BASE ? attempt(state)
      : url === `${BASE}/review` ? reviewPayload() : { files: [] } }))
    postRequest.mockImplementation(async () => {
      state = 'ai_resolving'
      return { data: { ok: true, result: { status: 'returned_to_resolver' } } }
    })
    const wrapper = realHost()
    await flushPromises()
    const dialog = wrapper.findComponent(GitMergeReviewDialog)
    useAiProviderStore().selectedProviderId = 'provider-0530'
    await flushPromises()
    await (dialog.vm as any).reject('keep both lines')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`${BASE}/reject`, {
      reason: 'keep both lines', provider_id: 'provider-0530', provider_pinned: true,
    })
    // The Manager may close the Host; a retained Host must still return to resolver mode.
    expect(wrapper.findComponent({ name: 'GitConflictResolverDialog' }).exists()).toBe(true)
    state = 'resolved_pending_review'
    await vi.advanceTimersByTimeAsync(3100)
    await flushPromises()
    expect(wrapper.findComponent(GitMergeReviewDialog).exists()).toBe(true)
    expect(postRequest).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})
