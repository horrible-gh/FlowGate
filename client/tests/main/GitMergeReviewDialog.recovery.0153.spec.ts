import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'
import GitConflictReviewHost from '@main/components/GitConflictReviewHost.vue'
import { createPinia } from 'pinia'
import { opensConflictReview } from '@main/composables/useConflictSession'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() }, getRequest, postRequest }))
const showToast = vi.fn()
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }))
const ATTEMPT = 'f13a930a-49c5-4b33-a91b-75736dce39f1'
function recovery(extra = {}) {
  return { merge_id: 4, session_state: 'open', review_state: null, approval_attempt_id: ATTEMPT,
    recovery_version: 'v1', reconciliation_kind: 'reconcile_auth_required', recovery_hold: { code: 'ttl' },
    git_terminal: false, approval: { approved: false, stage: 'pending', error: null },
    push_error: { cause: 'auth', returncode: 0, detail: '<img src=x onerror=alert(1)>' },
    preserved_refs: [{ ref: 'refs/flowgate/preserve/4', sha: 'a'.repeat(40), verification: 'verified' }],
    allowed_actions: ['retry', 'abort', 'refresh'], next_action: 'fix_auth', ...extra }
}
function payload(view: any = recovery()) {
  return { ok: true, result: { group_id: 'test2.default.0009', merge_id: 4,
    review_state: view?.review_state ?? 'resolved_pending_review', review_fingerprint: 'fp-1',
    approval_attempt_id: view?.approval_attempt_id, recovery: view, changes: [], conflict_origins: [],
    conversation: [], held_test_operations: [], pending_conversation: null, can_approve: !view,
    can_reject: !view, can_send: !view, resolver_provider: 'test' } }
}
const wrappers: any[] = []
function dialog() {
  const w = mount(GitMergeReviewDialog, { props: { groupId: 'test2.default.0009', mergeId: 4 },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } } })
  wrappers.push(w)
  return w
}
beforeEach(() => {
  i18n.global.locale.value = 'en'
  getRequest.mockReset(); postRequest.mockReset(); showToast.mockReset(); sessionStorage.clear()
  getRequest.mockResolvedValue({ data: payload() })
})
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); document.body.innerHTML = '' })

describe('0153 recovery', () => {
  it('U1 opens null-review holds and displays exact text, zero return code and verified refs', async () => {
    expect(opensConflictReview({ review_state: null, recovery: recovery() })).toBe(true)
    const w = dialog(); await flushPromises()
    expect(w.find('[data-test="git-recovery"]').exists()).toBe(true)
    expect(w.find('[data-test="push-evidence"]').text()).toContain('0')
    expect(w.find('[data-test="push-evidence"]').text()).toContain('<img src=x')
    expect(w.find('img').exists()).toBe(false)
    expect(w.text()).toContain('refs/flowgate/preserve/4')
    expect(w.find('[data-dialog-action-id="approve"]').exists()).toBe(false)
    expect(w.find('[data-dialog-action-id="reject"]').exists()).toBe(false)
  })
  it('U1 missing evidence disables mutation and remains refreshable', async () => {
    getRequest.mockResolvedValue({ data: payload(recovery({ allowed_actions: ['refresh'],
      next_action: 'refresh', push_error: null, preserved_refs: [], probe_error: { code: 'workspace_missing' } })) })
    const w = dialog(); await flushPromises()
    expect(w.text()).toContain('Not recorded / unverified')
    expect(w.find('[data-test="recovery-retry"]').exists()).toBe(false)
    expect(w.find('[data-test="recovery-abort"]').exists()).toBe(false)
    expect(w.find('[data-test="recovery-refresh"]').exists()).toBe(true)
  })
  it('U2 response loss retains request id across remount and releases busy', async () => {
    postRequest.mockRejectedValue(new Error('Network Error'))
    const w = dialog(); await flushPromises()
    await w.find('[data-test="recovery-retry"]').trigger('click'); await flushPromises()
    const first = postRequest.mock.calls[0][1]
    expect(w.find('[data-test="recovery-retry"]').attributes('disabled')).toBeUndefined()
    w.unmount()
    const next = dialog(); await flushPromises()
    await next.find('[data-test="recovery-retry"]').trigger('click'); await flushPromises()
    expect(postRequest.mock.calls[1][1]).toEqual(first)
    expect(first.approval_attempt_id).toBe(ATTEMPT)
    expect(next.emitted('close')).toBeUndefined()
  })
  it('U2 suppresses duplicate clicks and refreshes all surfaces after approval completion', async () => {
    let finish!: (value: any) => void
    postRequest.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const refresh = vi.fn(); window.addEventListener('fg:open_docs_refresh', refresh)
    const w = dialog(); await flushPromises()
    await w.find('[data-test="recovery-retry"]').trigger('click')
    await w.find('[data-test="recovery-retry"]').trigger('click')
    expect(postRequest).toHaveBeenCalledTimes(1)
    finish({ data: { ok: true, result: recovery({ git_terminal: true, session_state: 'done',
      approval: { approved: true, stage: 'complete', error: null }, allowed_actions: ['refresh'] }) } })
    await flushPromises()
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(w.emitted('close')).toHaveLength(1)
    window.removeEventListener('fg:open_docs_refresh', refresh)
  })
  it('U2 Git complete with approval failure stays open with approval-only retry', async () => {
    getRequest.mockResolvedValueOnce({ data: payload(null) })
    const failed = recovery({ git_terminal: true, session_state: 'done', review_state: 'completed',
      recovery_hold: null, approval: { approved: false, stage: 'approval_commit', error: { code: 'approval_commit_failed' } },
      allowed_actions: ['retry_approval','refresh'], next_action: 'retry_approval' })
    getRequest.mockResolvedValue({ data: payload(failed) })
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'merged', git_terminal: true,
      approval: failed.approval, error: failed.approval.error, recovery: failed } } })
    const w = dialog(); await flushPromises()
    await w.find('[data-dialog-action-id="approve"]').trigger('click'); await flushPromises()
    expect(w.emitted('close')).toBeUndefined()
    expect(showToast.mock.calls.some(c => c[1] === 'success')).toBe(false)
    expect(w.find('[data-test="approval-pending"]').exists()).toBe(true)
    expect(w.find('[data-test="recovery-retry"]').text()).toContain('approval only')
  })
  it('U2 abort uses the existing confirmation dialog and reports its preserved effect', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: recovery({ session_state: 'aborted',
      review_state: null, allowed_actions: ['refresh'], remote_outcome: 'unknown' }) } })
    const w = dialog(); await flushPromises()
    await w.find('[data-test="recovery-abort"]').trigger('click'); await flushPromises()
    expect(postRequest).not.toHaveBeenCalled()
    expect(w.text()).toContain('AC will remain unapproved')
    await w.find('[data-dialog-action-id="confirm"]').trigger('click'); await flushPromises()
    expect(postRequest.mock.calls[0][0]).toContain('/recovery/abort')
    expect(w.emitted('close')).toHaveLength(1)
  })
  it('U1 host opens a closed Git session with a pending approval', async () => {
    getRequest.mockImplementation((url: string) => Promise.resolve({ data: url.endsWith('/finalize') ?
      { ok: true, state: { status: 'merged', merge_id: 4, recovery: recovery({ git_terminal: true }) } } : payload() }))
    const w = mount(GitConflictReviewHost, { global: { plugins: [i18n, createPinia()],
      stubs: { GitMergeReviewDialog: { template: '<div data-test="opened" />' } } } })
    wrappers.push(w)
    await (w.vm as any).open({ group_id: 'test2.default.0009' }); await flushPromises()
    expect(w.find('[data-test="opened"]').exists()).toBe(true)
  })
})

describe('0153 request and display races', () => {
  it('U2 a stale version refreshes server truth and rotates the next explicit request', async () => {
    const newer = recovery({ recovery_version: 'v2' })
    postRequest.mockRejectedValueOnce({ response: { status: 409, data: { ok: false,
      error: { code: 'recovery_stale', details: { recovery: newer } } } } })
    const w = dialog(); await flushPromises()
    getRequest.mockResolvedValue({ data: payload(newer) })
    await w.find('[data-test="recovery-retry"]').trigger('click'); await flushPromises()
    const first = postRequest.mock.calls[0][1]
    expect(w.find('[data-test="recovery-retry"]').attributes('disabled')).toBeUndefined()
    postRequest.mockRejectedValue(new Error('Network Error'))
    await w.find('[data-test="recovery-retry"]').trigger('click'); await flushPromises()
    const second = postRequest.mock.calls[1][1]
    expect(second.request_id).not.toBe(first.request_id)
    expect(second.recovery_version).toBe('v2')
  })
  it('U2 an unresolved server failure retains the request receipt key and clears busy', async () => {
    postRequest.mockRejectedValue({ response: { status: 500, data: { ok: false,
      error: { code: 'recovery_failed', details: { recovery: recovery() } } } } })
    const w = dialog(); await flushPromises()
    for (let i = 0; i < 2; i++) {
      await w.find('[data-test="recovery-retry"]').trigger('click'); await flushPromises()
      expect(w.find('[data-test="recovery-retry"]').attributes('disabled')).toBeUndefined()
    }
    expect(postRequest.mock.calls[0][1]).toEqual(postRequest.mock.calls[1][1])
    expect(w.emitted('close')).toBeUndefined()
  })
  it('U2 a late read cannot overwrite an accepted abort response', async () => {
    const w = dialog(); await flushPromises()
    let finishRead!: (value: any) => void
    getRequest.mockImplementationOnce(() => new Promise(resolve => { finishRead = resolve }))
    const pendingRead = (w.vm as any).loadReview({ background: true })
    postRequest.mockResolvedValue({ data: { ok: true, result: recovery({ session_state: 'aborted',
      allowed_actions: ['refresh'] }) } })
    await w.find('[data-test="recovery-abort"]').trigger('click'); await flushPromises()
    await w.find('[data-dialog-action-id="confirm"]').trigger('click'); await flushPromises()
    finishRead({ data: payload() }); await pendingRead; await flushPromises()
    expect((w.vm as any).recovery.session_state).toBe('aborted')
    expect(w.emitted('close')).toHaveLength(1)
    expect(w.find('[data-test="recovery-retry"]').exists()).toBe(false)
  })
  it('U1 a recovery card offers review without AI or direct resolution', async () => {
    const { default: Card } = await import('@main/components/GitConflictCardActions.vue')
    const w = mount(Card, { props: { recovery: recovery() as any },
      global: { plugins: [i18n], stubs: { AppIcon: true } } })
    wrappers.push(w)
    expect(w.find('[data-test="conflict-card-ai"]').exists()).toBe(false)
    expect(w.find('[data-test="conflict-card-direct"]').exists()).toBe(false)
    await w.find('[data-test="conflict-card-recovery"]').trigger('click')
    expect(w.emitted('review')).toHaveLength(1)
  })
})


describe('0153 durable stop and new-round entry', () => {
  it('U2 resumes the server abort receipt after browser storage is lost', async () => {
    const pending = { action: 'abort', request_id: '23c01fb8-859b-4a7e-8da6-4341e84f477e',
      recovery_version: 'original-version', approval_attempt_id: ATTEMPT }
    getRequest.mockResolvedValue({ data: payload(recovery({ allowed_actions: ['refresh', 'abort'],
      next_action: 'resume_abort', pending_request: pending })) })
    postRequest.mockRejectedValue(new Error('Network Error'))
    const w = dialog(); await flushPromises()
    expect(w.find('[data-test="recovery-retry"]').exists()).toBe(false)
    expect(w.text()).toContain('Automatic publishing is paused')
    await w.find('[data-test="recovery-abort"]').trigger('click'); await flushPromises()
    await w.find('[data-dialog-action-id="confirm"]').trigger('click'); await flushPromises()
    const { action, ...body } = pending
    expect(action).toBe('abort')
    expect(postRequest.mock.calls[0][1]).toEqual(body)
    expect(w.find('[data-test="recovery-abort"]').attributes('disabled')).toBeUndefined()
  })

  it('U2 accepted preparation retains the request id until its one-shot cycle runs', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, result: recovery({
      receipt: { phase: 'reconcile_prepared', action: 'retry' } }) } })
    const w = dialog(); await flushPromises()
    await w.find('[data-test="recovery-retry"]').trigger('click'); await flushPromises()
    const first = postRequest.mock.calls[0][1]
    postRequest.mockRejectedValue(new Error('Network Error'))
    await w.find('[data-test="recovery-retry"]').trigger('click'); await flushPromises()
    expect(postRequest.mock.calls[1][1]).toEqual(first)
  })

  it('U1 opens preservation actions for a genuine new conflict without disabling its resolver', async () => {
    const view = recovery({ requires_recovery: false, review_state: null,
      reconciliation_kind: null, recovery_hold: null, approval_attempt_id: null,
      allowed_actions: ['refresh', 'resolve', 'abort'], next_action: 'resolve' })
    expect(opensConflictReview({ review_state: null, recovery: view })).toBe(false)
    getRequest.mockImplementation((url: string) => Promise.resolve({ data: url.endsWith('/finalize')
      ? { ok: true, state: { status: 'conflict', merge_id: 4, recovery: view } } : payload(view) }))
    const w = mount(GitConflictReviewHost, { global: { plugins: [i18n, createPinia()],
      stubs: { AppIcon: true, teleport: true } } })
    wrappers.push(w)
    await (w.vm as any).open({ group_id: 'test2.default.0009', recovery_only: true }); await flushPromises()
    expect(w.find('[data-test="git-recovery"]').exists()).toBe(true)
    expect(w.find('[data-test="recovery-abort"]').exists()).toBe(true)
    expect(w.find('[data-test="recovery-resume"]').exists()).toBe(true)
    expect(w.find('[data-dialog-action-id="approve"]').exists()).toBe(false)
  })
})


describe('TR0014 rev1 completed receipts and unknown remote outcome', () => {
  it('U2 first retry fails with a completed receipt, the cause is fixed, the next click is a new request and succeeds', async () => {
    const refreshed = recovery({ recovery_version: 'v2' })
    postRequest.mockImplementationOnce((_url: string, body: any) => Promise.reject({ response: { status: 409, data: { ok: false,
      error: { code: 'reconcile_auth_required', details: { recovery: { ...refreshed, request_id: body.request_id,
        receipt: { phase: 'completed', action: 'retry' } } } } } } }))
    const w = dialog(); await flushPromises()
    getRequest.mockResolvedValue({ data: payload(refreshed) })
    await w.find('[data-test="recovery-retry"]').trigger('click'); await flushPromises()
    const first = postRequest.mock.calls[0][1]
    expect(first.recovery_version).toBe('v1')
    expect(w.find('[data-test="recovery-retry"]').attributes('disabled')).toBeUndefined()
    expect(w.emitted('close')).toBeUndefined()
    // Cause fixed: the server accepts a new request with a new one-shot budget.
    postRequest.mockResolvedValueOnce({ data: { ok: true, result: recovery({ git_terminal: true, session_state: 'done',
      recovery_hold: null, approval: { approved: true, stage: 'complete', error: null }, allowed_actions: ['refresh'],
      receipt: { phase: 'completed', action: 'retry' } }) } })
    await w.find('[data-test="recovery-retry"]').trigger('click'); await flushPromises()
    const second = postRequest.mock.calls[1][1]
    expect(second.request_id).not.toBe(first.request_id)
    expect(second.recovery_version).toBe('v2')
    expect(second.approval_attempt_id).toBe(ATTEMPT)
    expect(w.emitted('close')).toHaveLength(1)
  })
  it('U2 a started receipt in the error envelope keeps the key for the resend', async () => {
    postRequest.mockImplementation((_url: string, body: any) => Promise.reject({ response: { status: 409, data: { ok: false,
      error: { code: 'preserve_failed', details: { recovery: { ...recovery(), request_id: body.request_id,
        receipt: { phase: 'started', action: 'retry' } } } } } } }))
    const w = dialog(); await flushPromises()
    for (let i = 0; i < 2; i++) {
      await w.find('[data-test="recovery-retry"]').trigger('click'); await flushPromises()
    }
    expect(postRequest.mock.calls[1][1]).toEqual(postRequest.mock.calls[0][1])
  })
  it('U2 a completed receipt of another request does not rotate this key', async () => {
    postRequest.mockImplementation(() => Promise.reject({ response: { status: 409, data: { ok: false,
      error: { code: 'reconcile_auth_required', details: { recovery: { ...recovery(),
        request_id: '0f6c4f3e-8f5f-4b1d-9d2a-111111111111', receipt: { phase: 'completed', action: 'retry' } } } } } } }))
    const w = dialog(); await flushPromises()
    for (let i = 0; i < 2; i++) {
      await w.find('[data-test="recovery-retry"]').trigger('click'); await flushPromises()
    }
    expect(postRequest.mock.calls[1][1]).toEqual(postRequest.mock.calls[0][1])
  })
  it('U1 an unrecorded lease with a failed remote read shows the unknown remote outcome', async () => {
    getRequest.mockResolvedValue({ data: payload(recovery({ allowed_actions: ['refresh', 'abort'],
      next_action: 'resume_abort', remote_outcome: 'unknown' })) })
    const w = dialog(); await flushPromises()
    expect(w.text()).toContain(i18n.global.t('main.git_review.recovery.remote_unknown'))
    w.unmount()
    getRequest.mockResolvedValue({ data: payload(recovery({ remote_outcome: 'not_published' })) })
    const control = dialog(); await flushPromises()
    expect(control.find('[data-test="git-recovery"]').exists()).toBe(true)
    expect(control.text()).not.toContain(i18n.global.t('main.git_review.recovery.remote_unknown'))
  })
})
