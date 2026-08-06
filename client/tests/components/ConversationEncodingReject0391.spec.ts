/**
 * 0391 T0005 §7-6 — the human chat screen must show the SERVER's rejection sentence.
 *
 * §5-4 makes `append_turn` raise ConversationTurnError(422, <why + how to pass>) for a
 * corrupted body, and documents/routers/conversation_turns.py re-raises it as
 * HTTPException(422, detail=<that same sentence>). The whole point of writing a long,
 * actionable Korean message server-side is lost if the browser collapses it into a
 * generic "등록 실패" — so this spec renders the REAL ConversationView together with the
 * REAL ToastContainer (useToast is deliberately NOT mocked, unlike ConversationView.spec.ts)
 * and asserts the server's sentence reaches the DOM verbatim.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

const { getRequest, postRequest, patchRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  patchRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  getRequest: (...a: unknown[]) => getRequest(...a),
  postRequest: (...a: unknown[]) => postRequest(...a),
  patchRequest: (...a: unknown[]) => patchRequest(...a),
}))

import ConversationView from '@main/components/ConversationView.vue'
import ToastContainer from '@main/components/common/ToastContainer.vue'
import { useToast } from '@main/components/common/useToast'

/** Mirrors ConversationView's SEND_FAILED_TOAST_MS. */
const SEND_FAILED_TOAST_MS = 15000

const DOC_ID = 'flowgate.default.0391.0009-CH'

// Byte-for-byte the string conversation_turn_service._encoding_violation() returns for a
// corrupted body (services/conversation_turn_service.py, 0391 T0005 §5-4/§5-6).
const SERVER_REJECT_SENTENCE =
  '본문이 깨진 글자(예: ??????)로 보입니다. 본문을 UTF-8 파일로 먼저 쓰고, 그 ' +
  '파일에서 글자 수와 해시(body_chars/body_sha256)를 구한 다음 다시 보내세요. ' +
  '정말 이대로 보내야 하면 force_encoding_reason에 사유(공백 제외 10자 이상)를 ' +
  '적어 다시 보내세요.'

const PROVIDERS_RESPONSE = {
  data: { ok: true, project: 'flowgate', providers: [], default_provider_id: null },
}
const CHAT_SETTINGS_DOMAIN = {
  send_action: ['copy_mention', 'invoke_ai', 'none'],
  context_mode: ['recent', 'all'],
  context_turns_presets: [5, 10, 15, 20, 30],
  context_turns_min: 1,
  context_turns_max: 200,
}
const CHAT_SETTINGS_DEFAULTS = { send_action: 'none', context_mode: 'recent', context_turns: 20 }

function turnsPage() {
  return {
    data: {
      ok: true,
      doc_id: DOC_ID,
      after_seq: 0,
      before_seq: null,
      limit: 50,
      head_seq: 0,
      next_after_seq: null,
      prev_before_seq: null,
      has_more: false,
      truncated_by: null,
      head: { intro: '', opening_turns: [], carried_over_from: null, total_turns: 0, head_seq: 0 },
      turns: [],
      participants: [],
      me: null,
    },
  }
}

/** axios-shaped 422 exactly as FastAPI's HTTPException(422, detail=...) arrives. */
function httpError(status: number, detail: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data: { detail } },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  // useToast keeps its queue in module scope, so a toast raised by an earlier test is
  // still on screen in the next one — reset it or every assertion here reads stale DOM.
  useToast().toasts.value = []
  getRequest.mockReset().mockImplementation((url: unknown) => {
    if (typeof url === 'string' && url.includes('ai-invoke/providers')) return Promise.resolve(PROVIDERS_RESPONSE)
    if (typeof url === 'string' && url.includes('/me/chat-settings')) {
      return Promise.resolve({
        data: {
          ok: true,
          settings: { ...CHAT_SETTINGS_DEFAULTS, updated_at: null },
          is_default: true,
          defaults: CHAT_SETTINGS_DEFAULTS,
          domain: CHAT_SETTINGS_DOMAIN,
        },
      })
    }
    return Promise.resolve(turnsPage())
  })
  postRequest.mockReset()
  patchRequest.mockReset().mockResolvedValue({ data: { ok: true } })
})

/** Mount the chat view and a live ToastContainer over the same useToast singleton. */
function mountChatWithToasts() {
  const pinia = createPinia()
  const view = mount(ConversationView, {
    props: { docId: DOC_ID, projectId: 'flowgate' },
    global: { plugins: [i18n, pinia] },
  })
  // ToastContainer teleports to body; stub the teleport so its markup stays inside the
  // wrapper and can be asserted on (teleported nodes leave an empty wrapper otherwise).
  const toasts = mount(ToastContainer, {
    global: { plugins: [i18n, pinia], stubs: { teleport: true } },
  })
  return { view, toasts }
}

async function sendAndFail(detail: unknown) {
  const { view, toasts } = mountChatWithToasts()
  await flushPromises()
  postRequest.mockRejectedValue(httpError(422, detail))
  // `.conv-input` explicitly: the manual-copy panel also renders a textarea, so a bare
  // find('textarea') can silently target the wrong one and leave the draft empty (which
  // disables the send button and makes every assertion below pass vacuously).
  await view.find('textarea.conv-input').setValue('??? ??? ??? ??')
  expect(view.find('.conv-send').attributes('disabled')).toBeUndefined()
  // The composer is a <form @submit.prevent="send">; the send button's own click handler
  // only serves stop-mode, so submitting the form is what actually sends.
  await view.find('form').trigger('submit')
  await flushPromises()
  expect(postRequest).toHaveBeenCalled()
  await toasts.vm.$nextTick()
  return { view, toasts }
}

describe('0391 §7-6 — chat 422 shows the server reason on screen', () => {
  it('renders the whole server sentence in the toast, not a generic failure line', async () => {
    const { toasts } = await sendAndFail(SERVER_REJECT_SENTENCE)

    const shown = toasts.text()
    expect(toasts.find('.toast-danger').exists()).toBe(true)
    // Verbatim: every clause the server wrote — (가) it looks corrupted, (나) write a
    // UTF-8 file first, (다) or fill in force_encoding_reason — survives to the DOM.
    expect(shown).toContain(SERVER_REJECT_SENTENCE)
    expect(shown).toContain('깨진 글자')
    expect(shown).toContain('UTF-8 파일로 먼저')
    expect(shown).toContain('force_encoding_reason')
  })

  it('renders the fingerprint-mismatch reason including both counts', async () => {
    const detail =
      '본문 지문이 어긋납니다: 글자수 기대=120 실제=97. 본문을 UTF-8 파일로 먼저 쓰고 ' +
      '그 파일에서 글자 수와 해시를 구해 다시 보내거나, force_encoding_reason에 ' +
      '사유(공백 제외 10자 이상)를 적어 다시 보내세요.'
    const { toasts } = await sendAndFail(detail)
    expect(toasts.text()).toContain(detail)
    expect(toasts.text()).toContain('기대=120')
    expect(toasts.text()).toContain('실제=97')
  })

  it('keeps the draft so the user can act on what the reason told them', async () => {
    const { view } = await sendAndFail(SERVER_REJECT_SENTENCE)
    expect((view.find('textarea.conv-input').element as HTMLTextAreaElement).value).not.toBe('')
  })

  it('gives a rejection long enough to read more than the default 3s toast life', async () => {
    // Regression guard for the §7-6 fix: an actionable 422 must not vanish on the
    // generic 3000ms timer — the reason is ~150 characters of instructions, which is
    // not readable in 3 seconds. Fake timers are installed BEFORE the send so the
    // dismissal timer the toast schedules is the fake one.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const { toasts } = await sendAndFail(SERVER_REJECT_SENTENCE)
      expect(toasts.find('.toast-danger').exists()).toBe(true)
      vi.advanceTimersByTime(3500)
      await toasts.vm.$nextTick()
      expect(toasts.find('.toast-danger').exists()).toBe(true)
      // ...and it does eventually go away on its own — no permanently stuck banner.
      vi.advanceTimersByTime(SEND_FAILED_TOAST_MS)
      await toasts.vm.$nextTick()
      expect(toasts.find('.toast-danger').exists()).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('lets the user dismiss the rejection by clicking it', async () => {
    // The other half of the long life: a 15s toast the user cannot close would be in
    // the way. Clicking anywhere on it removes it immediately.
    const { toasts } = await sendAndFail(SERVER_REJECT_SENTENCE)
    await toasts.find('.toast-danger').trigger('click')
    expect(toasts.find('.toast-danger').exists()).toBe(false)
  })
})
