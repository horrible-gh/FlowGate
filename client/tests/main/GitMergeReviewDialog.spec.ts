// flowgate.default.0481 T0008 (D0006 §6.3 / L0007 §2.11) — the general-merge
// human approval gate screen: real diff + conflict-origin overlay, provider
// badge, conversation/approve/reject, all against the four new review windows.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

function reviewPayload(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    result: {
      group_id: 'flowgate.default.0481',
      merge_id: 9,
      review_state: 'resolved_pending_review',
      review_fingerprint: 'fp-1',
      instruction_generation: 0,
      base_head: 'base1',
      merge_head: 'merge1',
      snapshot_tree: 'tree1',
      changes: [
        { path: 'server/app/git_service.py', status: 'M', old_path: null },
        { path: 'client/src/App.vue', status: 'A', old_path: null },
      ],
      conflict_origins: [
        {
          path: 'server/app/git_service.py', chunk_id: 'c1', selection: 'ours',
          start_line: 2, end_line: 2, range_ambiguous: false,
        },
      ],
      conversation: [],
      held_test_operations: [],
      resolver_provider: 'Claude Sonnet 5',
      auto_authority: false,
      reconciliation_kind: null,
      last_error: null,
      can_approve: true,
      can_reject: true,
      can_send: true,
      ...overrides,
    },
  }
}

function diffPayload(path: string) {
  return {
    ok: true,
    data: {
      group_id: 'flowgate.default.0481', merge_id: 9, path, status: 'M',
      old: { exists: true, binary: false, truncated: false, size: 5, content: 'a\nb\n' },
      new: { exists: true, binary: false, truncated: false, size: 5, content: 'a\nc\n' },
    },
  }
}

function mountDialog() {
  return mount(GitMergeReviewDialog, {
    props: {
      groupId: 'flowgate.default.0481',
      mergeId: 9,
      branch: 'group/0481',
      baseBranch: 'main',
      providers: [{ id: 'p1', name: 'Claude Sonnet 5' }],
      selectedProvider: 'p1',
    },
    global: {
      plugins: [i18n],
      stubs: { AppIcon: true, teleport: true },
    },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/review-diff')) return Promise.resolve({ data: diffPayload('server/app/git_service.py') })
    if (url.includes('/review')) return Promise.resolve({ data: reviewPayload() })
    return Promise.reject(new Error('unexpected ' + url))
  })
})

describe('GitMergeReviewDialog', () => {
  it('loads the review screen: provider badge, warning, file list, and real diff', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    expect(wrapper.text()).toContain('Claude Sonnet 5')
    expect(wrapper.text()).toContain('Not committed yet')
    expect(wrapper.findAll('.gcd-file')).toHaveLength(2)
    expect(wrapper.text()).toContain('server/app/git_service.py')
    expect(wrapper.text()).toContain('1 conflict chunk')

    wrapper.unmount()
  })

  it('sends attempt_id + fingerprint on approve and closes on completion', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'completed', merge_commit: 'abc1234' } } })
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.gmr-ft-actions .btn-primary').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/groups/flowgate.default.0481/git/merge/9/approve',
      expect.objectContaining({ review_fingerprint: 'fp-1' }),
    )
    const [, body] = postRequest.mock.calls[0]
    expect(typeof body.attempt_id).toBe('string')
    expect(body.attempt_id.length).toBeGreaterThan(0)
    expect(wrapper.emitted('resolved')).toHaveLength(1)
    expect(wrapper.emitted('close')).toHaveLength(1)

    wrapper.unmount()
  })

  it('re_review keeps the dialog open and reloads instead of closing', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 're_review', review_fingerprint: 'fp-2' } } })
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.gmr-ft-actions .btn-primary').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('close')).toBeUndefined()
    expect(wrapper.emitted('resolved')).toBeUndefined()

    wrapper.unmount()
  })

  it('reject requires a reason, sends provider_pinned=true, and returns to the resolver', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'returned_to_resolver', review_state: null } } })
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.gmr-ft-actions .btn-danger-ol').trigger('click')
    expect(wrapper.find('.gmr-reject-overlay').exists()).toBe(true)
    expect(wrapper.find('.gmr-reject-actions .btn-danger-ol').attributes('disabled')).toBeDefined()

    await wrapper.find('.gmr-reject-box textarea').setValue('please redo the yaml side')
    expect(wrapper.find('.gmr-reject-actions .btn-danger-ol').attributes('disabled')).toBeUndefined()
    await wrapper.find('.gmr-reject-actions .btn-danger-ol').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/groups/flowgate.default.0481/git/merge/9/reject',
      { reason: 'please redo the yaml side', provider_id: 'p1', provider_pinned: true },
    )
    expect(wrapper.emitted('resolved')).toHaveLength(1)
    expect(wrapper.emitted('close')).toHaveLength(1)

    wrapper.unmount()
  })

  it('sends a review message with the chosen provider pinned and apply_requested reflecting the toggle', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'accepted', run_id: 'run1' } } })
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.gmr-conv-compose textarea').setValue('why did you change ko.ts?')
    await wrapper.find('.gmr-apply-toggle input').setValue(true)
    await wrapper.find('.gmr-send-btn').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/groups/flowgate.default.0481/git/merge/9/review-message',
      {
        message: 'why did you change ko.ts?', provider_id: 'p1', provider_pinned: true,
        apply_requested: true, allow_test_edits: false,
      },
    )

    wrapper.unmount()
  })

  it('shows held test operations and lets the second explicit action re-instruct with allow_test_edits', async () => {
    // 0009-TR rev3 (AI review finding 2): held_test_operations must be shown,
    // and the ONLY way to send allow_test_edits=true is this dedicated
    // [테스트 편집 포함 재지시] action — never the ordinary apply toggle/send.
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/review-diff')) return Promise.resolve({ data: diffPayload('server/app/git_service.py') })
      if (url.includes('/review')) {
        return Promise.resolve({
          data: reviewPayload({
            held_test_operations: [
              { operation_id: 'held1', kind: 'create_file', path: 'server/tests/new_case.py', purpose: 'regression case' },
            ],
          }),
        })
      }
      return Promise.reject(new Error('unexpected ' + url))
    })
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'accepted', run_id: 'run1' } } })
    const wrapper = mountDialog()
    await flushPromises()

    expect(wrapper.text()).toContain('server/tests/new_case.py')
    expect(wrapper.text()).toContain('regression case')

    await wrapper.find('.gmr-conv-compose textarea').setValue('include the held test edit too')
    await wrapper.find('.gmr-allow-test-edits-btn').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/groups/flowgate.default.0481/git/merge/9/review-message',
      {
        message: 'include the held test edit too', provider_id: 'p1', provider_pinned: true,
        apply_requested: true, allow_test_edits: true,
      },
    )

    wrapper.unmount()
  })

  it('does not show the second-authorization button when there are no held test operations', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    expect(wrapper.find('.gmr-allow-test-edits-btn').exists()).toBe(false)
    expect(wrapper.find('.gmr-held-tests').exists()).toBe(false)

    wrapper.unmount()
  })

  it('reconciling disables approve and reject', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/review-diff')) return Promise.resolve({ data: diffPayload('server/app/git_service.py') })
      if (url.includes('/review')) {
        return Promise.resolve({
          data: reviewPayload({ review_state: 'reconciling', can_approve: false, can_reject: false, can_send: false }),
        })
      }
      return Promise.reject(new Error('unexpected ' + url))
    })
    const wrapper = mountDialog()
    await flushPromises()

    expect(wrapper.find('.gmr-ft-actions .btn-primary').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.gmr-ft-actions .btn-danger-ol').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Confirming the outcome')

    wrapper.unmount()
  })
})
