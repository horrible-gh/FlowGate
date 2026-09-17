// flowgate.default.0570 T0004 §2.1/§2.2 — the review conversation gets the same
// STOP control the general chat already has (0264 R0001's ConversationView.vue):
// while an AI run is in flight, [전송] becomes a cancel button that calls the
// existing POST /api/v1/ai-invoke/{run_id}/cancel, and a cancelled run must not
// read like an ordinary failure once it folds back into the conversation.
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

type Turn = { turn_id: string; role: 'human' | 'ai'; message: string; provider_id: string | null; status: string; created_at: string }

function turn(role: 'human' | 'ai', message: string, status = 'accepted'): Turn {
  return { turn_id: `${role}-${message}`, role, message, provider_id: 'p1', status, created_at: '2026-09-17T00:00:00+09:00' }
}

const state: { conversation: Turn[]; pending: Record<string, unknown> | null } = { conversation: [], pending: null }

function reviewPayload() {
  return {
    ok: true,
    result: {
      group_id: 'flowgate.default.0570',
      merge_id: 9,
      review_state: 'resolved_pending_review',
      review_fingerprint: 'fp-1',
      instruction_generation: 0,
      base_head: 'base1',
      merge_head: 'merge1',
      snapshot_tree: 'tree1',
      changes: [{ path: 'server/app/git_service.py', status: 'M', old_path: null }],
      conflict_origins: [],
      conversation: state.conversation,
      held_test_operations: [],
      pending_conversation: state.pending,
      resolver_provider: 'Claude Sonnet 5',
      auto_authority: false,
      reconciliation_kind: null,
      last_error: null,
      can_approve: true,
      can_reject: true,
      can_send: true,
    },
  }
}

function diffPayload(path: string) {
  return {
    ok: true,
    data: {
      group_id: 'flowgate.default.0570', merge_id: 9, path, status: 'M',
      old: { exists: true, binary: false, truncated: false, size: 5, content: 'a\nb\n' },
      new: { exists: true, binary: false, truncated: false, size: 5, content: 'a\nc\n' },
    },
  }
}

function mountDialog() {
  return mount(GitMergeReviewDialog, {
    props: {
      groupId: 'flowgate.default.0570',
      mergeId: 9,
      branch: 'group/0570',
      baseBranch: 'main',
      providers: [{ id: 'p1', name: 'Claude Sonnet 5' }],
      selectedProvider: 'p1',
    },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
}

/** Let every scheduled poll between now and `ms` run to completion. */
async function advance(ms: number) {
  const step = 500
  for (let waited = 0; waited < ms; waited += step) {
    await vi.advanceTimersByTimeAsync(step)
    await flushPromises()
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  state.conversation = []
  state.pending = null
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/review-diff')) return Promise.resolve({ data: diffPayload('server/app/git_service.py') })
    if (url.includes('/review')) return Promise.resolve({ data: reviewPayload() })
    return Promise.reject(new Error('unexpected ' + url))
  })
  postRequest.mockImplementation((url: string) => {
    if (url.includes('/review-message')) return Promise.resolve({ data: { ok: true, result: { status: 'accepted', run_id: 'run-1' } } })
    if (url.endsWith('/cancel')) return Promise.resolve({ data: { ok: true, status: 'cancelling' } })
    return Promise.reject(new Error('unexpected ' + url))
  })
})

afterEach(() => {
  vi.useRealTimers()
})

async function sendChat(wrapper: ReturnType<typeof mountDialog>, text = 'why is this taking so long?') {
  await wrapper.find('.gmr-conv-compose textarea').setValue(text)
  // The server persists the human turn and reports the run it started.
  state.conversation = [...state.conversation, turn('human', text)]
  state.pending = { run_id: 'run-1', status: 'running', provider: 'Claude Sonnet 5', started_at: null, elapsed_ms: 0, write_requested: false, allow_test_edits: false }
  await wrapper.find('.gmr-send-btn').trigger('click')
  await flushPromises()
}

describe('GitMergeReviewDialog — STOP for the review conversation (flowgate.default.0570 T0004)', () => {
  it('실행 중이 아닐 때는 전송 버튼이 평소대로 동작한다', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    expect(wrapper.find('.gmr-send-btn').attributes('title')).toBeUndefined()

    await wrapper.find('.gmr-conv-compose textarea').setValue('hello')
    await wrapper.find('.gmr-send-btn').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/groups/flowgate.default.0570/git/merge/9/review-message',
      expect.objectContaining({ message: 'hello' }),
    )
    wrapper.unmount()
  })

  it('pendingConversation이 있으면 버튼이 STOP 상태로 전환된다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await sendChat(wrapper)

    const send = wrapper.find('.gmr-send-btn')
    expect(send.attributes('title')).toContain(i18n.global.t('main.ai_invoke_dialog.btn_cancel_run'))
    expect(send.attributes('disabled')).toBeUndefined()

    wrapper.unmount()
  })

  it('STOP 클릭 시 정확한 run_id로 cancel API를 호출한다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await sendChat(wrapper)

    await wrapper.find('.gmr-send-btn').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/run-1/cancel', {})

    wrapper.unmount()
  })

  it('취소 요청 중 중복 클릭은 두 번째 cancel 호출을 만들지 않는다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await sendChat(wrapper)

    let resolveCancel: (() => void) | undefined
    postRequest.mockImplementationOnce(() => new Promise((resolve) => {
      resolveCancel = () => resolve({ data: { ok: true, status: 'cancelling' } })
    }))

    const send = wrapper.find('.gmr-send-btn')
    await send.trigger('click')
    await flushPromises()
    await send.trigger('click') // duplicate click while the first cancel is still in flight
    await flushPromises()

    const cancelCalls = postRequest.mock.calls.filter((c: any[]) => String(c[0]).endsWith('/cancel'))
    expect(cancelCalls).toHaveLength(1)

    resolveCancel?.()
    await flushPromises()
    wrapper.unmount()
  })

  it('취소 후 기존 polling이 서버의 최종 상태(취소됨)를 화면에 반영한다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await sendChat(wrapper)

    await wrapper.find('.gmr-send-btn').trigger('click') // STOP
    await flushPromises()

    // The server settles the run: pending clears and a `cancelled` AI turn appears —
    // exactly the shape _materialize_pending_conversation_run produces (T0004 §2.2).
    state.pending = null
    state.conversation = [...state.conversation, turn('ai', '사용자가 이 실행을 중지했습니다(취소됨).', 'cancelled')]
    await advance(3_000)

    expect(wrapper.find('[data-test="gmr-waiting-turn"]').exists()).toBe(false)
    const badges = wrapper.findAll('[data-test="gmr-turn-status"]').map((n) => n.text())
    expect(badges).toContain(i18n.global.t('main.git_review.turn_status_cancelled'))
    expect(badges).not.toContain(i18n.global.t('main.git_review.turn_status_failed'))

    // The button is a send button again, ready for the next instruction.
    expect(wrapper.find('.gmr-send-btn').attributes('title')).toBeUndefined()

    wrapper.unmount()
  })

  it('취소가 끝난 뒤 다시 대화를 진행할 수 있다', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await sendChat(wrapper)

    await wrapper.find('.gmr-send-btn').trigger('click') // STOP
    await flushPromises()
    state.pending = null
    state.conversation = [...state.conversation, turn('ai', '사용자가 이 실행을 중지했습니다(취소됨).', 'cancelled')]
    await advance(3_000)

    await wrapper.find('.gmr-conv-compose textarea').setValue('다시 물어볼게')
    state.conversation = [...state.conversation, turn('human', '다시 물어볼게')]
    state.pending = { run_id: 'run-2', status: 'running', provider: 'Claude Sonnet 5', started_at: null, elapsed_ms: 0, write_requested: false, allow_test_edits: false }
    await wrapper.find('.gmr-send-btn').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/groups/flowgate.default.0570/git/merge/9/review-message',
      expect.objectContaining({ message: '다시 물어볼게' }),
    )

    wrapper.unmount()
  })
})
