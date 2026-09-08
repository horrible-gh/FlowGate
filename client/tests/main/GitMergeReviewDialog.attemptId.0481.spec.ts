// flowgate.default.0481 T0010 rev4 — "attempt_id must be a UUID" (2026-09-08 12:29).
//
// The reviewer's [승인] never reached `approve_merge_review`: the route validates
// `attempt_id` against a UUID regex and answered `400 invalid_attempt_id`, because on the
// insecure (HTTP LAN) origin the app is served from, `crypto.randomUUID` does not exist and
// the dialog's fallback produced `1a07f14f5ae-8ce03764272e48-...`. This pins the contract at
// the real call site: whatever the browser can offer, the body this dialog POSTs is accepted
// by the server's own regex.
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

/** The exact regex `server/modules/flow_gate/api/v1/git_routes.py` validates against. */
const SERVER_UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/

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
      changes: [{ path: 'src/data/tasks.ts', status: 'M', old_path: null }],
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
  await wrapper.findAll('.gmr-ft-actions button').at(1)!.trigger('click')
  await flushPromises()
}

function approveBody() {
  const call = postRequest.mock.calls.find((c) => String(c[0]).endsWith('/approve'))
  expect(call, 'the dialog never POSTed the approve route').toBeTruthy()
  return call![1] as { attempt_id: string; review_fingerprint: string }
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
  postRequest.mockResolvedValue({
    data: { ok: true, result: { status: 'merged', review_state: 'completed', merge_commit: '03cdd70' } },
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('GitMergeReviewDialog attempt_id (0481 T0010 rev4)', () => {
  it('sends a server-valid UUID on an insecure origin, where crypto.randomUUID is absent', async () => {
    // Exactly what Chrome exposes at http://192.168.0.252:8080: Web Crypto without randomUUID.
    vi.stubGlobal('crypto', {
      getRandomValues: (buf: Uint8Array) => {
        for (let i = 0; i < buf.length; i++) buf[i] = Math.floor(Math.random() * 256)
        return buf
      },
    })
    const wrapper = mountDialog()
    await flushPromises()

    await approve(wrapper)

    expect(approveBody().attempt_id).toMatch(SERVER_UUID_RE)
  })

  it('sends a server-valid UUID on a secure origin too', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    await approve(wrapper)

    expect(approveBody().attempt_id).toMatch(SERVER_UUID_RE)
  })

  it('mints a fresh but still server-valid id after an approval that did not merge', async () => {
    // A refused approval deliberately regenerates the attempt id so the next press is a new
    // attempt rather than a replay of the failed one — a second place the old fallback ran.
    vi.stubGlobal('crypto', {
      getRandomValues: (buf: Uint8Array) => {
        for (let i = 0; i < buf.length; i++) buf[i] = Math.floor(Math.random() * 256)
        return buf
      },
    })
    postRequest.mockResolvedValue({
      data: { ok: true, result: { status: 'pre_commit_validation_failed', review_state: 'resolved_pending_review', errors: [] } },
    })
    const wrapper = mountDialog()
    await flushPromises()

    await approve(wrapper)
    await approve(wrapper)

    const attempts = postRequest.mock.calls
      .filter((c) => String(c[0]).endsWith('/approve'))
      .map((c) => (c[1] as { attempt_id: string }).attempt_id)
    expect(attempts).toHaveLength(2)
    for (const id of attempts) expect(id).toMatch(SERVER_UUID_RE)
    expect(attempts[1]).not.toBe(attempts[0])
  })
})
