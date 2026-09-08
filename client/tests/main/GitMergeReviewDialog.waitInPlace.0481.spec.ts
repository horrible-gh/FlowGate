// flowgate.default.0481 T0010 rev1 — 승인 대기 화면의 채팅은 "치고 기다리면 여기로 답이
// 온다"여야 한다. 반려 사유(2026-09-08): "난 채팅을 치면 기다렸다가 바로 답장 받는걸
// 원했는데 아예 다이얼로그 밖으로 빠져나가서 기본 AI실행 다이얼로그 보는걸 원하지 않는다."
//
// 이 시험이 지키는 것 네 가지:
//   1) 폴링이 화면을 통째로 로딩 스피너로 지웠다 되돌리지 않는다(2초마다 깜빡였다).
//   2) [전송] 즉시 대화 로그 안에 대기 줄이 서고, 답은 그 자리에 도착한다.
//   3) 옛 40틱(80초) 상한을 넘겨도 계속 기다린다 — 실제 실행은 그보다 오래 걸린다.
//   4) 이미 실행이 떠 있는 채로 화면을 열어도(로컬 상태 없음) 그 대기를 이어받는다.
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

function turn(role: 'human' | 'ai', message: string): Turn {
  return { turn_id: `${role}-${message}`, role, message, provider_id: 'p1', status: 'accepted', created_at: '2026-09-08T00:00:00+09:00' }
}

/** The one payload every test drives; `state` is mutated between polls. */
const state: { conversation: Turn[]; pending: Record<string, unknown> | null } = { conversation: [], pending: null }

function reviewPayload() {
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
  postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'accepted', run_id: 'run-1' } } })
})

afterEach(() => {
  vi.useRealTimers()
})

async function sendChat(wrapper: ReturnType<typeof mountDialog>, text = 'why did you touch ko.ts?') {
  await wrapper.find('.gmr-conv-compose textarea').setValue(text)
  // The server persists the human turn and reports the run it started.
  state.conversation = [...state.conversation, turn('human', text)]
  state.pending = { run_id: 'run-1', status: 'running', provider: 'Claude Sonnet 5', started_at: null, elapsed_ms: 0, write_requested: false, allow_test_edits: false }
  await wrapper.find('.gmr-send-btn').trigger('click')
  await flushPromises()
}

describe('GitMergeReviewDialog — waiting for the reply in place (0481 T0010 rev1)', () => {
  it('shows the wait inside the conversation log and keeps the screen intact while polling', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    await sendChat(wrapper)

    // The human turn is on screen at once, with the reply's placeholder under it.
    expect(wrapper.text()).toContain('why did you touch ko.ts?')
    expect(wrapper.find('[data-test="gmr-waiting-turn"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Claude Sonnet 5 is writing an answer')
    // ...and sending again is blocked, visibly, with the reason on the control.
    const send = wrapper.find('.gmr-send-btn')
    expect(send.attributes('disabled')).toBeDefined()
    expect(send.attributes('title')).toContain('is writing an answer')

    // Ten seconds of polling: the dialog never falls back to the full-screen
    // loading/error states, and the file list + diff stay put.
    await advance(10_000)
    expect(wrapper.find('.gmr-state').exists()).toBe(false)
    expect(wrapper.findAll('.gcd-file')).toHaveLength(1)
    expect(wrapper.text()).toContain('server/app/git_service.py')
    expect(wrapper.emitted('close')).toBeUndefined()

    wrapper.unmount()
  })

  it('keeps waiting past the old 80-second ceiling and lands the reply in the log', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await sendChat(wrapper)

    // The old loop stopped after 40 ticks / 80s. Go well beyond that and the
    // screen must still be listening.
    await advance(180_000)
    expect(wrapper.find('[data-test="gmr-waiting-turn"]').exists()).toBe(true)
    const pollsBefore = getRequest.mock.calls.filter((c: any[]) => String(c[0]).endsWith('/review')).length
    expect(pollsBefore).toBeGreaterThan(40)

    // The run finishes: the server folds the answer in and clears the pending block.
    state.conversation = [...state.conversation, turn('ai', 'I added the key GitStatusPanel.vue now uses.')]
    state.pending = null
    await advance(20_000)

    expect(wrapper.text()).toContain('I added the key GitStatusPanel.vue now uses.')
    expect(wrapper.find('[data-test="gmr-waiting-turn"]').exists()).toBe(false)
    expect(wrapper.find('.gmr-send-btn').attributes('disabled')).toBeDefined() // draft is empty again
    expect(wrapper.emitted('close')).toBeUndefined()

    // Polling stops once there is nothing to wait for.
    const pollsAtRest = getRequest.mock.calls.filter((c: any[]) => String(c[0]).endsWith('/review')).length
    await advance(60_000)
    expect(getRequest.mock.calls.filter((c: any[]) => String(c[0]).endsWith('/review')).length).toBe(pollsAtRest)

    wrapper.unmount()
  })

  it('picks up a run that was already in flight when the screen was opened', async () => {
    state.conversation = [turn('human', 'take another look at ko.ts')]
    state.pending = { run_id: 'run-9', status: 'running', provider: 'GPT-5', started_at: null, elapsed_ms: 65_000, write_requested: false, allow_test_edits: false }

    const wrapper = mountDialog()
    await flushPromises()

    // No local send happened here — this is server truth alone.
    expect(wrapper.find('[data-test="gmr-waiting-turn"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('GPT-5 is writing an answer')
    expect(wrapper.text()).toContain('1:05') // anchored to the run's own elapsed time

    const before = getRequest.mock.calls.filter((c: any[]) => String(c[0]).endsWith('/review')).length
    await advance(30_000)
    expect(getRequest.mock.calls.filter((c: any[]) => String(c[0]).endsWith('/review')).length).toBeGreaterThan(before)

    wrapper.unmount()
  })

  it('a failed poll neither blanks the screen nor gives up', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await sendChat(wrapper)

    getRequest.mockImplementationOnce(() => Promise.reject({ response: { data: { error: { message: 'boom' } } } }))
    await advance(6_000)

    expect(wrapper.find('.gmr-state-error').exists()).toBe(false)
    expect(wrapper.text()).toContain('why did you touch ko.ts?')
    expect(wrapper.find('[data-test="gmr-waiting-turn"]').exists()).toBe(true)

    wrapper.unmount()
  })
})
