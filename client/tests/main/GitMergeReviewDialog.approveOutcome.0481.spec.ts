// flowgate.default.0481 T0010 rev3 — "머지는 되지도 않음" (2026-09-08 10:33).
//
// The server's real answer for the reviewer's own merge was
// `pre_commit_validation_failed` with two per-file diagnostics attached. The dialog
// named exactly three statuses and dropped everything else into one vanishing
// danger toast with no status, no file and no reason — so [승인] looked like it did
// nothing, every time. These pin the rule: an approval that does not merge must say
// on screen what came back.
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

const showToast = vi.fn()
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

function reviewPayload() {
  return {
    ok: true,
    result: {
      group_id: 'test2.default.0009',
      merge_id: 4,
      review_state: 'resolved_pending_review',
      review_fingerprint: 'fp-1',
      instruction_generation: 0,
      base_head: 'e25a7e8',
      merge_head: 'ee2709f',
      snapshot_tree: 'ad32e91',
      changes: [
        { path: 'src/data/tasks.ts', status: 'M', old_path: null },
        { path: 'tsconfig.app.tsbuildinfo', status: 'A', old_path: null },
      ],
      conflict_origins: [],
      conversation: [],
      held_test_operations: [],
      pending_conversation: null,
      resolver_provider: 'Claude Haiku 4.5',
      auto_authority: false,
      reconciliation_kind: null,
      last_error: null,
      can_approve: true,
      can_reject: true,
      can_send: true,
    },
  }
}

function mountDialog() {
  return mount(GitMergeReviewDialog, {
    props: {
      groupId: 'test2.default.0009',
      mergeId: 4,
      branch: 'test2_default_0009',
      baseBranch: 'main',
      providers: [{ id: 'p1', name: 'Claude Haiku 4.5' }],
      selectedProvider: 'p1',
    },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
}

async function approve(wrapper: ReturnType<typeof mountDialog>) {
  const button = wrapper.findAll('.gmr-ft-actions button').at(1)!
  await button.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/review-diff')) {
      return Promise.resolve({
        data: {
          ok: true,
          data: {
            group_id: 'test2.default.0009', merge_id: 4, path: 'src/data/tasks.ts', status: 'M',
            old: { exists: true, binary: false, truncated: false, size: 2, content: 'a\n' },
            new: { exists: true, binary: false, truncated: false, size: 2, content: 'b\n' },
          },
        },
      })
    }
    if (url.includes('/review')) return Promise.resolve({ data: reviewPayload() })
    return Promise.reject(new Error('unexpected ' + url))
  })
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('GitMergeReviewDialog approve outcome (0481 T0010 rev3)', () => {
  it('names the blocked files when the pre-commit check refuses the merge', async () => {
    postRequest.mockResolvedValue({
      data: {
        ok: true,
        result: {
          status: 'pre_commit_validation_failed',
          review_state: 'resolved_pending_review',
          errors: [
            { path: 'tsconfig.app.tsbuildinfo', validator: 'unsupported', line: null, message: "no syntax validator registered for '.tsbuildinfo'" },
            { path: 'src/data/tasks.ts', validator: 'ecmascript', line: 12, message: 'Unexpected token' },
          ],
        },
      },
    })
    const wrapper = mountDialog()
    await flushPromises()

    await approve(wrapper)

    const box = wrapper.find('[data-test="gmr-approve-outcome"]')
    expect(box.exists()).toBe(true)
    expect(box.text()).toContain('tsconfig.app.tsbuildinfo')
    expect(box.text()).toContain("no syntax validator registered for '.tsbuildinfo'")
    expect(box.text()).toContain('src/data/tasks.ts')
    expect(box.text()).toContain('12')
    // The dialog stays open — the operator has something to act on.
    expect(wrapper.emitted('close')).toBeUndefined()
  })

  it('names a status it has no sentence for instead of going quiet', async () => {
    postRequest.mockResolvedValue({
      data: { ok: true, result: { status: 'some_future_state', review_state: 'resolved_pending_review' } },
    })
    const wrapper = mountDialog()
    await flushPromises()

    await approve(wrapper)

    const box = wrapper.find('[data-test="gmr-approve-outcome"]')
    expect(box.exists()).toBe(true)
    expect(box.text()).toContain('some_future_state')
  })

  it('keeps the reason on screen after the refresh that follows a failed approval', async () => {
    // The refresh must be a BACKGROUND one: a full reload raises the loading gate,
    // which replaces the dialog body — and the reason with it.
    postRequest.mockResolvedValue({
      data: { ok: true, result: { status: 'commit_creation_failed', review_state: 'resolved_pending_review' } },
    })
    const wrapper = mountDialog()
    await flushPromises()

    await approve(wrapper)
    await flushPromises()

    expect(wrapper.find('[data-test="gmr-approve-outcome"]').exists()).toBe(true)
    expect(wrapper.find('.gmr-ft-actions').exists()).toBe(true)
  })

  it('closes on a real merge and says nothing was blocked', async () => {
    postRequest.mockResolvedValue({
      data: { ok: true, result: { status: 'merged', review_state: 'completed', merge_commit: '03cdd70', pushed: true } },
    })
    const wrapper = mountDialog()
    await flushPromises()

    await approve(wrapper)

    expect(wrapper.find('[data-test="gmr-approve-outcome"]').exists()).toBe(false)
    expect(wrapper.emitted('resolved')).toBeTruthy()
    expect(wrapper.emitted('close')).toBeTruthy()
  })
})
