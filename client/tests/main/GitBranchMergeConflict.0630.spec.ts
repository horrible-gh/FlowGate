/**
 * flowgate.default.0630 T0005 §12/§15-23 — the 0.1 Branch Manager reaches the EXISTING
 * conflict resolver and merge review for an ordinary branch merge.
 *
 *   - GitBranchMergeConflictHost draws nothing of its own: it mounts the existing
 *     GitConflictResolverDialog / GitMergeReviewDialog, feeds them from the project-scoped
 *     routes, and picks between them on the SERVER attempt state.
 *   - the resolver dialog's two opt-outs hide exactly the [자동] authority toggle and the
 *     [멘트 복사] action — nothing else moves.
 *   - the review dialog talks to the route base it is given, and never says "the AI
 *     resolved it" for a manual resolution.
 */
import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

import GitBranchMergeConflictHost from '@main/components/GitBranchMergeConflictHost.vue'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'
import { parseConflictFile, type ConflictFileState } from '@main/composables/useConflictChunks'

const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  getRequest,
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

// The Branch Manager's own "run this merge?" confirm is answered yes; every other dialog
// (the resolver, the review) is the real one on the real dialog stack.
vi.mock('@main/composables/useDialogStack', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  confirm: () => Promise.resolve(true),
}))

const BASE = '/api/v1/projects/flowgate/git/merge/42'
const CONFLICT = ['<<<<<<< HEAD', 'develop line', '=======', 'feature line', '>>>>>>> feature', ''].join('\n')

function attempt(state: string, ai: Record<string, unknown> = {}) {
  return {
    ok: true,
    result: {
      merge_id: 42, state, source_branch: 'feature', target_branch: 'develop', push: false,
      file_count: 1, resolved_count: 0,
      ai: { status: 'running', run_id: 'run-1', provider_id: 'Claude', error: null, ...ai },
    },
  }
}

function routeGets(map: Record<string, unknown>) {
  getRequest.mockImplementation((url: string) => {
    if (url in map) return Promise.resolve({ data: map[url] })
    return Promise.resolve({ data: {} })
  })
}

function mountHost() {
  return mount(GitBranchMergeConflictHost, {
    props: { projectId: 'flowgate', mergeId: 42 },
    global: {
      plugins: [i18n],
      stubs: {
        AppIcon: true,
        GitConflictResolverDialog: {
          name: 'GitConflictResolverDialog',
          props: ['files', 'branch', 'baseBranch', 'busy', 'loadStatus', 'errorMessage', 'providers',
            'selectedProvider', 'providerLoading', 'providerErrored', 'aiRunNotice', 'aiRunPending',
            'hideAutoAuthority', 'hideCopyMention'],
          template: '<div data-test="resolver-stub" />',
        },
        GitMergeReviewDialog: {
          name: 'GitMergeReviewDialog',
          props: ['groupId', 'mergeId', 'mergeApiBase', 'branch', 'baseBranch', 'providers',
            'selectedProvider', 'providerLoading', 'providerErrored'],
          template: '<div data-test="review-stub" />',
        },
      },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  ;(Element.prototype as any).scrollTo = vi.fn()
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
})

afterEach(() => {
  resetDialogSystem()
  vi.useRealTimers()
})

describe('GitBranchMergeConflictHost — server state picks the existing dialog', () => {
  it('an AI-resolving attempt opens the existing resolver on the project routes, AI half wired, [자동]/[멘트 복사] off', async () => {
    routeGets({
      [BASE]: attempt('ai_resolving'),
      [`${BASE}/conflicts`]: { ok: true, files: [{ path: 'same.txt', content: CONFLICT, conflict_count: 1 }] },
    })
    const wrapper = mountHost()
    await flushPromises()

    const resolver = wrapper.findComponent({ name: 'GitConflictResolverDialog' })
    expect(resolver.exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'GitMergeReviewDialog' }).exists()).toBe(false)
    expect(getRequest).toHaveBeenCalledWith(BASE)
    expect(getRequest).toHaveBeenCalledWith(`${BASE}/conflicts`)
    expect(resolver.props('files')).toHaveLength(1)
    expect(resolver.props('branch')).toBe('feature')
    expect(resolver.props('baseBranch')).toBe('develop')
    expect(resolver.props('hideAutoAuthority')).toBe(true)
    expect(resolver.props('hideCopyMention')).toBe(true)
    // the running notice comes from the SERVER state, not from any AI message
    expect(String(resolver.props('aiRunNotice'))).toContain('Claude')
    wrapper.unmount()
  })

  it('an AI start failure keeps the resolver open and says why (manual fallback)', async () => {
    routeGets({
      [BASE]: attempt('conflict_remaining', { status: 'start_failed', run_id: null,
        error: { code: 'no_enabled_provider', message: 'no provider' } }),
      [`${BASE}/conflicts`]: { ok: true, files: [{ path: 'same.txt', content: CONFLICT, conflict_count: 1 }] },
    })
    const wrapper = mountHost()
    await flushPromises()
    const resolver = wrapper.findComponent({ name: 'GitConflictResolverDialog' })
    expect(resolver.props('aiRunNotice')).toBeNull()
    expect(String(resolver.props('errorMessage'))).toContain('no provider')
    wrapper.unmount()
  })

  it('a resolved attempt opens the existing review dialog on the project route base', async () => {
    routeGets({ [BASE]: attempt('resolved_pending_review') })
    const wrapper = mountHost()
    await flushPromises()
    const review = wrapper.findComponent({ name: 'GitMergeReviewDialog' })
    expect(review.exists()).toBe(true)
    expect(review.props('mergeApiBase')).toBe(BASE)
    expect(review.props('mergeId')).toBe(42)
    expect(wrapper.findComponent({ name: 'GitConflictResolverDialog' }).exists()).toBe(false)
    wrapper.unmount()
  })

  it('submit / AI re-invoke / abort go to the project-scoped routes', async () => {
    routeGets({
      [BASE]: attempt('conflict_remaining', { status: 'finished' }),
      [`${BASE}/conflicts`]: { ok: true, files: [{ path: 'same.txt', content: 'merged\n', conflict_count: 0 }] },
    })
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'running' } } })
    const wrapper = mountHost()
    await flushPromises()
    const resolver = wrapper.findComponent({ name: 'GitConflictResolverDialog' })

    resolver.vm.$emit('submit', false)
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`${BASE}/resolve`, {
      files: [{ path: 'same.txt', content: 'merged\n' }], complete: true,
    })

    resolver.vm.$emit('ai-invoke', 'keep both', false)
    await flushPromises()
    const aiCall = postRequest.mock.calls.find(([url]) => url === `${BASE}/ai-resolve`)
    expect(aiCall?.[1]).toMatchObject({ message: 'keep both' })
    expect(aiCall?.[1]).not.toHaveProperty('auto')

    resolver.vm.$emit('abort')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`${BASE}/abort`, {})
    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })

  it('while the AI resolves, the attempt is re-read in the background and hands over to review', async () => {
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
    expect(reads).toBe(2)
    expect(wrapper.findComponent({ name: 'GitMergeReviewDialog' }).exists()).toBe(true)
    wrapper.unmount()
  })
})

/* ───────────── end to end in the client: Branch Manager → existing resolver → existing review ───────────── */

describe('0.1 Branch Manager conflict → existing resolver/review (T0005 §15-23, nothing stubbed but HTTP)', () => {
  it('merge → 202 conflict → [충돌 해결 열기] → real resolver → submit → real review on the project routes', async () => {
    const { default: GitBranchManager } = await import('@main/components/GitBranchManager.vue')
    const catalog = {
      ok: true, base_branch: 'main', default_merge_target: null,
      branches: [
        { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
        { name: 'develop', kind: 'local', can_delete: true, can_be_create_source: true },
        { name: 'feature', kind: 'local', can_delete: true, can_be_create_source: true },
      ],
    }
    let attemptState = 'conflict_remaining'
    getRequest.mockImplementation((url: string) => {
      if (url === '/api/v1/projects/flowgate/git/branches') return Promise.resolve({ data: catalog })
      if (url === BASE) {
        return Promise.resolve({ data: attempt(attemptState, { status: 'start_failed', run_id: null,
          error: { code: 'no_enabled_provider', message: 'no provider' } }) })
      }
      if (url === `${BASE}/conflicts`) {
        return Promise.resolve({ data: { ok: true, files: [{ path: 'same.txt', content: CONFLICT, conflict_count: 1 }] } })
      }
      if (url === `${BASE}/review`) {
        return Promise.resolve({ data: { ok: true, result: {
          merge_id: 42, review_state: 'resolved_pending_review', review_fingerprint: 'fp', instruction_generation: 0,
          base_head: 'b', merge_head: 'm', snapshot_tree: 't', changes: [], conflict_origins: [],
          conversation: [], held_test_operations: [], resolver_provider: null, resolver_type: 'human',
          auto_authority: false, reconciliation_kind: null, last_error: null,
          can_approve: true, can_reject: true, can_send: true,
        } } })
      }
      return Promise.resolve({ data: {} })
    })
    postRequest.mockImplementation((url: string) => {
      if (url === '/api/v1/projects/flowgate/git/branches/merge') {
        return Promise.resolve({ data: { ok: true, status: 'conflict', merge_id: 42, conflict_files: ['same.txt'],
          push: false, pushed: false, ai: { status: 'start_failed' } } })
      }
      if (url === `${BASE}/resolve`) {
        attemptState = 'resolved_pending_review'
        return Promise.resolve({ data: { ok: true, result: { status: 'resolved_pending_review' } } })
      }
      return Promise.resolve({ data: { ok: true } })
    })
    const wrapper = mount(GitBranchManager, {
      props: { projectId: 'flowgate' },
      global: { plugins: [i18n], stubs: { AppIcon: true } },
      attachTo: document.body,
    })
    await flushPromises()
    ;(wrapper.vm as any).mergeSource = 'feature'
    ;(wrapper.vm as any).mergeTarget = 'develop'
    await flushPromises()
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/projects/flowgate/git/branches/merge',
      { source_branch: 'feature', target_branch: 'develop', push: true })
    expect(wrapper.get('[data-test="merge-conflict-ai"]').text())
      .toBe(i18n.global.t('main.git_branch_manager.merge_conflict_ai.start_failed'))
    await wrapper.get('[data-test="merge-conflict-open"]').trigger('click')
    await flushPromises()

    // the REAL resolver dialog, fed from the project-scoped routes, [자동]/[멘트 복사] off
    expect(document.querySelector('.git-conflict-auto-toggle')).toBeNull()
    expect(actionIds()).toEqual(['ai-invoke', 'abort', 'submit'])
    expect(document.body.textContent).toContain('same.txt')
    expect(document.body.textContent).toContain('no provider')

    // resolve the one chunk by hand, then submit through the real footer button
    const file = wrapper.findComponent(GitConflictResolverDialog).props('files')[0] as ConflictFileState
    file.mode = 'direct'
    file.directText = 'merged by hand\n'
    await flushPromises()
    document.querySelector<HTMLButtonElement>('[data-dialog-action-id="submit"]')?.click()
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`${BASE}/resolve`, {
      files: [{ path: 'same.txt', content: 'merged by hand\n' }], complete: true,
    })

    // the host re-reads the SERVER state and hands over to the REAL review dialog
    expect(wrapper.findComponent(GitMergeReviewDialog).exists()).toBe(true)
    expect(getRequest).toHaveBeenCalledWith(`${BASE}/review`)
    expect(document.querySelector('[data-test="gmr-resolved-by"]')?.textContent?.trim())
      .toBe(i18n.global.t('main.git_review.resolved_by_human'))
    wrapper.unmount()
  })
})

/* ─────────────────────────── the reused dialogs' two small seams ─────────────────────────── */

function makeFile(path: string): ConflictFileState {
  const segments = parseConflictFile(CONFLICT)
  if (!segments) throw new Error('fixture must parse')
  return { path, conflict_count: 1, directText: CONFLICT, mode: 'chunk', segments, notice: '' }
}

function actionIds(): string[] {
  return [...document.querySelectorAll('.fg-dialog-footer [data-dialog-action-id]')]
    .map((el) => el.getAttribute('data-dialog-action-id') ?? '')
}

describe('GitConflictResolverDialog opt-outs (0630)', () => {
  function open(extra: Record<string, unknown> = {}) {
    return mount(GitConflictResolverDialog, {
      props: {
        files: [makeFile('same.txt')], branch: 'feature', baseBranch: 'develop', busy: false,
        loadStatus: 'ready' as const, errorMessage: '',
        providers: [{ id: 'p1', name: 'Claude' }], selectedProvider: 'p1', ...extra,
      },
      global: { plugins: [i18n], stubs: { AppIcon: true } },
      attachTo: document.body,
    })
  }

  it('without the opt-outs the group screens keep [자동] and [멘트 복사]', async () => {
    const wrapper = open()
    await flushPromises()
    expect(actionIds()).toEqual(['copy-mention', 'ai-invoke', 'abort', 'submit'])
    expect(document.querySelector('.git-conflict-auto-toggle')).not.toBeNull()
    wrapper.unmount()
  })

  it('the branch-merge host hides exactly those two controls', async () => {
    const wrapper = open({ hideAutoAuthority: true, hideCopyMention: true })
    await flushPromises()
    expect(actionIds()).toEqual(['ai-invoke', 'abort', 'submit'])
    expect(document.querySelector('.git-conflict-auto-toggle')).toBeNull()
    expect(new DOMWrapper(document.body).find('[data-test="conflict-ai-message"]').exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('GitMergeReviewDialog route base + resolver type (0630)', () => {
  function review(overrides: Record<string, unknown> = {}) {
    return {
      ok: true,
      result: {
        merge_id: 42, review_state: 'resolved_pending_review', review_fingerprint: 'fp',
        instruction_generation: 0, base_head: 'b', merge_head: 'm', snapshot_tree: 't',
        changes: [], conflict_origins: [], conversation: [], held_test_operations: [],
        resolver_provider: null, auto_authority: false, reconciliation_kind: null,
        last_error: null, can_approve: true, can_reject: true, can_send: true,
        ...overrides,
      },
    }
  }

  function openReview() {
    return mount(GitMergeReviewDialog, {
      props: { groupId: '', mergeId: 42, mergeApiBase: BASE, branch: 'feature', baseBranch: 'develop',
        providers: [{ id: 'p1', name: 'Claude' }], selectedProvider: 'p1' },
      global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
    })
  }

  it('reads the review from the project-scoped base and says a person wrote a manual resolution', async () => {
    getRequest.mockResolvedValue({ data: review({ resolver_type: 'human' }) })
    const wrapper = openReview()
    await flushPromises()
    expect(getRequest.mock.calls[0][0]).toBe(`${BASE}/review`)
    const badge = wrapper.get('[data-test="gmr-resolved-by"]').text()
    expect(badge).toBe(i18n.global.t('main.git_review.resolved_by_human'))
    // no "run record's provider" note and no unknown-provider placeholder for a manual resolution
    expect(badge).not.toContain(i18n.global.t('main.git_review.unknown_provider'))
    expect(badge).not.toContain(i18n.global.t('main.git_review.provider_badge_note'))
    wrapper.unmount()
  })

  it('a mixed resolution names the provider AND the person', async () => {
    getRequest.mockResolvedValue({ data: review({ resolver_type: 'mixed', resolver_provider: 'Claude' }) })
    const wrapper = openReview()
    await flushPromises()
    expect(wrapper.get('[data-test="gmr-resolved-by"] span').text())
      .toBe(i18n.global.t('main.git_review.resolved_by_mixed', { provider: 'Claude' }))
    wrapper.unmount()
  })

  it('approve posts to the project-scoped base', async () => {
    getRequest.mockResolvedValue({ data: review({ resolver_type: 'ai', resolver_provider: 'Claude' }) })
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'merged', merge_commit: 'abc1234', pushed: false } } })
    const wrapper = openReview()
    await flushPromises()
    await (wrapper.vm as any).approve()
    await flushPromises()
    expect(postRequest.mock.calls.some(([url]) => url === `${BASE}/approve`)).toBe(true)
    wrapper.unmount()
  })
})
