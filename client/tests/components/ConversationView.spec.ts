import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

// Group 0235 (D0005 §3-2 / P0007 §0-1 / L0008): ConversationView replaces the old
// "auto-copy" checkbox with a "send-time action" radio group (copy_mention /
// invoke_ai / none). On a successful send it dispatches the chosen action — copy the
// mention ({ auto: true }), run the in-app AI call, or nothing. The old boolean key
// is migrated to the new key on first read. Never dispatches on a failed send.
const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  showToast: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  getRequest: (...a: unknown[]) => getRequest(...a),
  postRequest: (...a: unknown[]) => postRequest(...a),
}))
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

import ConversationView from '@main/components/ConversationView.vue'

const SEND_ACTION_KEY = 'flowgate.chat.sendAction'
const LEGACY_AUTOCOPY_KEY = 'flowgate.chat.autoCopyMention'
const DOC_ID = 'flowgate.default.0085.0009-CH'
const OTHER_DOC_ID = 'flowgate.default.0224.0005-CH'

function draftKey(docId = DOC_ID, userId = 'guest') {
  return 'flowgate.user.' + userId + '.chat.drafts.' + docId
}

const PROVIDERS_RESPONSE = {
  data: {
    ok: true,
    project: 'flowgate',
    providers: [{ id: 'p1', name: 'P1', exec_type: 'api', kind: 'openai' }],
    default_provider_id: 'p1',
  },
}

// 0351 T2: the conversation is loaded as TURNS through a cursor endpoint, not as a
// markdown body — these helpers build the P0003 §0-2 wire shape the view now consumes.
function turn(seq: number, over: Partial<Record<string, unknown>> = {}) {
  return {
    seq,
    speaker: 'user',
    participant_key: 'user:u1',
    display_name: 'sjm',
    locale: 'ko',
    body: `turn ${seq}`,
    based_on_seq: seq - 1,
    stale_since_seq: null,
    source_run_id: null,
    created_at: '2026-07-29T10:00:00+09:00',
    ...over,
  }
}

function turnsPage(rows: unknown[] = [], over: Partial<Record<string, unknown>> = {}) {
  const seqs = rows.map((r) => (r as { seq: number }).seq)
  return {
    data: {
      ok: true,
      doc_id: DOC_ID,
      after_seq: 0,
      before_seq: null,
      limit: 50,
      head_seq: seqs.length ? Math.max(...seqs) : 0,
      next_after_seq: null,
      prev_before_seq: null,
      has_more: false,
      truncated_by: null,
      head: { intro: '', opening_turns: [], carried_over_from: null, total_turns: rows.length, head_seq: seqs.length ? Math.max(...seqs) : 0 },
      turns: rows,
      participants: [],
      me: null,
      ...over,
    },
  }
}

// getRequest serves BOTH the conversation turn pages and the provider list. Default:
// one enabled provider present, so the "Call AI" radio/button is available.
function withProviders(rows: unknown[] = []) {
  getRequest.mockImplementation((url: unknown) => {
    if (typeof url === 'string' && url.includes('ai-invoke/providers')) {
      return Promise.resolve(PROVIDERS_RESPONSE)
    }
    return Promise.resolve(turnsPage(rows))
  })
}

function withoutProviders() {
  getRequest.mockImplementation((url: unknown) => {
    if (typeof url === 'string' && url.includes('ai-invoke/providers')) {
      return Promise.resolve({ data: { ok: true, project: 'flowgate', providers: [], default_provider_id: null } })
    }
    return Promise.resolve(turnsPage())
  })
}

function mountView(docId = DOC_ID) {
  return mount(ConversationView, {
    props: { docId, projectId: 'flowgate' },
    global: { plugins: [i18n, createPinia()] },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'en'
  localStorage.clear()
  delete window.__accessToken__
  getRequest.mockReset()
  withProviders()
  postRequest.mockReset().mockResolvedValue({
    data: { ok: true, doc_id: DOC_ID, replayed: false, head_seq: 1, turn: turn(1), me: null },
  })
  showToast.mockReset()
})

describe('ConversationView inline provider selector', () => {
  it('renders the shared provider selector immediately before the message input', async () => {
    const wrapper = mountView()
    await flushPromises()
    const selector = wrapper.find('.conv-provider-select')
    const input = wrapper.find('.conv-input')
    expect(selector.exists()).toBe(true)
    expect(selector.element.nextElementSibling).toBe(input.element)
    expect((selector.find('select').element as HTMLSelectElement).value).toBe('p1')
  })

  it('uses a provider selected beside the input for the next chat AI call', async () => {
    getRequest.mockImplementation((url: unknown) => {
      if (typeof url === 'string' && url.includes('ai-invoke/providers')) {
        return Promise.resolve({
          data: {
            ok: true,
            project: 'flowgate',
            providers: [
              { id: 'p1', name: 'P1', exec_type: 'api', kind: 'openai' },
              { id: 'p2', name: 'P2', exec_type: 'cli', kind: 'codex' },
            ],
            default_provider_id: 'p1',
          },
        })
      }
      return Promise.resolve(turnsPage())
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('.conv-provider-select select').setValue('p2')
    postRequest.mockReset().mockRejectedValueOnce({ response: { data: { code: 'run_in_progress', run_id: 'r9' } } })
    const buttons = wrapper.findAll('.conv-assist-btn')
    await buttons[buttons.length - 1].trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/ai-invoke/start',
      expect.objectContaining({ provider_id: 'p2', action_scope: 'chat' }),
    )
  })
})

describe('ConversationView send-time action', () => {
  it('renders the send-time action radios instead of a checkbox', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
    expect(wrapper.find('input[type="radio"][value="copy_mention"]').exists()).toBe(true)
    expect(wrapper.find('input[type="radio"][value="invoke_ai"]').exists()).toBe(true)
    expect(wrapper.find('input[type="radio"][value="none"]').exists()).toBe(true)
  })

  it('emits copy-mention with { auto: true } after a successful send when set to copy_mention', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('input[type="radio"][value="copy_mention"]').setValue()
    await wrapper.find('textarea').setValue('hello worker')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('copy-mention')).toEqual([[{ auto: true }]])
  })

  it('runs the in-app AI call after a successful send when set to invoke_ai', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('input[type="radio"][value="invoke_ai"]').setValue()
    await wrapper.find('textarea').setValue('hello worker')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    // First POST is the turn; second is the immediate chat AI invoke.
    expect(postRequest).toHaveBeenCalledTimes(2)
    expect(postRequest).toHaveBeenLastCalledWith(
      '/api/v1/ai-invoke/start',
      expect.objectContaining({
        project: 'flowgate',
        module: 'default',
        group: '0085',
        doc_ref: DOC_ID,
        action_scope: 'chat',
        mode: 'single',
      }),
    )
    expect(wrapper.emitted('copy-mention')).toBeUndefined()
  })

  // 0351 T2 / P0003 시나리오 3: the request carries the trimmed body, a per-message
  // idempotency key and what the sender had actually read. `speaker` is gone — the
  // server decides the author from the session, so claiming one is meaningless.
  it('posts the trimmed body with an idempotency key and the read position', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('  hello worker  ')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/documents/flowgate.default.0085.0009-CH/conversation/turn',
      expect.objectContaining({ body: 'hello worker', based_on_seq: 0 }),
    )
    const payload = postRequest.mock.calls[0][1] as { idempotency_key: string; speaker?: string }
    expect(payload.idempotency_key).toMatch(/^sess_/)
    expect(payload.speaker).toBeUndefined()
  })

  it('reuses the same idempotency key when a retry follows a transient failure', async () => {
    // A retry that minted a new key would store the message twice; the same key makes
    // the server replay instead (P0003 시나리오 4).
    postRequest
      .mockRejectedValueOnce({ response: { status: 503 } })
      .mockResolvedValueOnce({
        data: { ok: true, doc_id: DOC_ID, replayed: false, head_seq: 1, turn: turn(1), me: null },
      })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('hello worker')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    const first = postRequest.mock.calls[0][1] as { idempotency_key: string }
    const second = postRequest.mock.calls[1][1] as { idempotency_key: string }
    expect(postRequest).toHaveBeenCalledTimes(2)
    expect(second.idempotency_key).toBe(first.idempotency_key)
  })

  it('does not retry a decision the server already made', async () => {
    // 422 is an answer, not a blip — repeating it just repeats the same rejection.
    postRequest.mockRejectedValue({ response: { status: 422, data: { detail: 'Turn body must not be empty.' } } })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('hello worker')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.conv-row.is-failed').exists()).toBe(true)
  })

  it('renders FastAPI validation details as readable toast text', async () => {
    postRequest.mockRejectedValue({
      response: {
        status: 422,
        data: {
          detail: [
            { loc: ['body', 'body'], msg: 'Field required', type: 'missing' },
            { loc: ['body', 'idempotency_key'], msg: 'Field required' },
          ],
        },
      },
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('hello worker')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(showToast).toHaveBeenCalledWith(
      'Failed to send message: body.body: Field required (missing); body.idempotency_key: Field required',
      'danger',
    )
  })

  it('does NOT dispatch any action when set to none (default)', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('hello worker')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('copy-mention')).toBeUndefined()
  })

  it('does NOT dispatch when the send fails', async () => {
    postRequest.mockReset().mockRejectedValueOnce(new Error('boom'))
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('input[type="radio"][value="copy_mention"]').setValue()
    await wrapper.find('textarea').setValue('hello worker')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.emitted('copy-mention')).toBeUndefined()
  })

  it('still emits copy-mention (no payload) from the manual button', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('.conv-assist-btn').trigger('click')
    expect(wrapper.emitted('copy-mention')).toEqual([[]])
  })

  it('migrates the legacy auto-copy key to the new send-action key on first read', async () => {
    localStorage.setItem(LEGACY_AUTOCOPY_KEY, '1')
    const wrapper = mountView()
    await flushPromises()
    expect(localStorage.getItem(SEND_ACTION_KEY)).toBe('copy_mention')
    expect(localStorage.getItem(LEGACY_AUTOCOPY_KEY)).toBeNull()
    expect(
      (wrapper.find('input[type="radio"][value="copy_mention"]').element as HTMLInputElement).checked,
    ).toBe(true)
  })

  it('persists the selection and restores it on remount', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('input[type="radio"][value="copy_mention"]').setValue()
    expect(localStorage.getItem(SEND_ACTION_KEY)).toBe('copy_mention')

    const wrapper2 = mountView()
    await flushPromises()
    expect(
      (wrapper2.find('input[type="radio"][value="copy_mention"]').element as HTMLInputElement).checked,
    ).toBe(true)
  })

  it('disables the "Call AI" radio and hides the manual button when no provider exists', async () => {
    withoutProviders()
    const wrapper = mountView()
    await flushPromises()
    expect(
      (wrapper.find('input[type="radio"][value="invoke_ai"]').element as HTMLInputElement).disabled,
    ).toBe(true)
    // Only the [Copy mention] button remains in the assist row.
    expect(wrapper.findAll('.conv-assist-btn').length).toBe(1)
  })

  it('reverts a stale invoke_ai selection to none once the empty provider list resolves', async () => {
    localStorage.setItem(SEND_ACTION_KEY, 'invoke_ai')
    withoutProviders()
    const wrapper = mountView()
    await flushPromises()
    expect(localStorage.getItem(SEND_ACTION_KEY)).toBe('none')
    expect(
      (wrapper.find('input[type="radio"][value="none"]').element as HTMLInputElement).checked,
    ).toBe(true)
  })
})

// Group 0235 — the chat AI invoke path (D0005 §3-1, L0008 §2-3 / §3 / §5). Covers the
// provider gating derived from the doc id (the reported "provider is registered but
// [Call AI] doesn't show" regression) and the start-time 409 / failure contract, which
// depends on ai_invoke_routes._err() flattening {code, run_id} to the top level.
describe('ConversationView chat AI invoke', () => {
  it('derives the project from the doc id when the tab passes no projectId, so a registered provider still enables Call AI', async () => {
    // GroupExplorer.openDocument opened CH tabs without a projectId; gating must fall
    // back to the project code embedded in the doc id instead of hiding [Call AI].
    const wrapper = mount(ConversationView, {
      props: { docId: DOC_ID, projectId: null },
      global: { plugins: [i18n, createPinia()] },
    })
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/providers', { project: 'flowgate' })
    expect(
      (wrapper.find('input[type="radio"][value="invoke_ai"]').element as HTMLInputElement).disabled,
    ).toBe(false)
    // Both [Copy mention] and [Call AI] are present.
    expect(wrapper.findAll('.conv-assist-btn').length).toBe(2)
  })

  it('absorbs a 409 run_in_progress from start without surfacing an error toast', async () => {
    const wrapper = mountView()
    await flushPromises()
    postRequest.mockReset()
    postRequest.mockRejectedValueOnce({ response: { data: { code: 'run_in_progress', run_id: 'r9' } } })
    const btns = wrapper.findAll('.conv-assist-btn')
    await btns[btns.length - 1].trigger('click') // manual [Call AI]
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/ai-invoke/start',
      expect.objectContaining({ action_scope: 'chat' }),
    )
    // The existing run is adopted (spinner keeps polling), never surfaced as a failure.
    expect(showToast).not.toHaveBeenCalled()
  })

  it('shows the invoke-failed toast when start fails for a non-409 reason', async () => {
    const wrapper = mountView()
    await flushPromises()
    postRequest.mockReset()
    postRequest.mockRejectedValueOnce({ response: { data: { detail: 'server exploded' } } })
    const btns = wrapper.findAll('.conv-assist-btn')
    await btns[btns.length - 1].trigger('click') // manual [Call AI]
    await flushPromises()
    expect(showToast).toHaveBeenCalledWith('AI call failed: server exploded', 'danger')
  })

  // 0264 R0001: chat AI progress must show on the SEND button (no dialog), and it must be
  // actionable rather than a passive spinner — while a chat AI run is in flight the send
  // button becomes a STOP button that stays ENABLED so the run can be cancelled.
  it('turns the SEND button into an enabled STOP button while a chat AI call is running', async () => {
    const wrapper = mountView()
    await flushPromises()
    // Send button idle before any run: enabled once a draft exists, plane icon.
    await wrapper.find('textarea').setValue('review this')
    expect(wrapper.find('.conv-send').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.conv-send').classes()).not.toContain('is-stop')

    postRequest.mockReset()
    postRequest.mockResolvedValueOnce({ data: { ok: true, run_id: 'r1' } }) // start → run in flight
    const btns = wrapper.findAll('.conv-assist-btn')
    await btns[btns.length - 1].trigger('click') // manual [Call AI]
    await flushPromises()

    // The polling loop keeps invoking=true (its first tick is behind a 2.5s timer), so
    // the send button reflects the in-flight AI call as a live stop control.
    const send = wrapper.find('.conv-send')
    expect(send.classes()).toContain('is-stop')
    expect(send.attributes('disabled')).toBeUndefined()
    expect(send.attributes('type')).toBe('button') // must not fall through to a send
    expect(send.attributes('title')).toBe('Cancel run')
  })

  it('cancels the running chat AI call when the STOP button is clicked', async () => {
    const wrapper = mountView()
    await flushPromises()
    postRequest.mockReset()
    postRequest.mockResolvedValueOnce({ data: { ok: true, run_id: 'r1' } }) // start
    const btns = wrapper.findAll('.conv-assist-btn')
    await btns[btns.length - 1].trigger('click') // manual [Call AI] → invoking
    await flushPromises()

    postRequest.mockClear()
    postRequest.mockResolvedValueOnce({ data: { ok: true, run_id: 'r1', status: 'cancelling' } })
    await wrapper.find('.conv-send').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/r1/cancel', {})
    // Cancelling is an interim state: the button waits for the run to actually finish.
    const send = wrapper.find('.conv-send')
    expect(send.attributes('disabled')).toBeDefined()
    expect(send.attributes('title')).toBe('Cancelling — terminating the process tree…')
  })

  it('stops a run it adopted rather than started, using the adopted run id', async () => {
    const wrapper = mountView()
    await flushPromises()
    postRequest.mockReset()
    // 409: the group already has a run — adopted through the run_in_progress path.
    postRequest.mockRejectedValueOnce({ response: { data: { code: 'run_in_progress', run_id: 'r9' } } })
    const btns = wrapper.findAll('.conv-assist-btn')
    await btns[btns.length - 1].trigger('click')
    await flushPromises()

    postRequest.mockClear()
    postRequest.mockResolvedValueOnce({ data: { ok: true, run_id: 'r9', status: 'cancelling' } })
    await wrapper.find('.conv-send').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/r9/cancel', {})
  })

  it('re-enables the STOP button and toasts when the cancel request fails', async () => {
    const wrapper = mountView()
    await flushPromises()
    postRequest.mockReset()
    postRequest.mockResolvedValueOnce({ data: { ok: true, run_id: 'r1' } })
    const btns = wrapper.findAll('.conv-assist-btn')
    await btns[btns.length - 1].trigger('click')
    await flushPromises()

    postRequest.mockClear()
    postRequest.mockRejectedValueOnce({ response: { status: 500 } })
    await wrapper.find('.conv-send').trigger('click')
    await flushPromises()

    expect(showToast).toHaveBeenCalledWith('Failed to cancel the run.', 'danger')
    // The run is still up, so the user must be able to try again.
    const send = wrapper.find('.conv-send')
    expect(send.classes()).toContain('is-stop')
    expect(send.attributes('disabled')).toBeUndefined()
  })

  it('does not report a user-initiated stop as a failure', async () => {
    vi.useFakeTimers()
    const wrapper = mountView()
    try {
      await flushPromises()
      // A killed run finishes with no new AI turn — the exact shape of a failed run.
      getRequest.mockImplementation((url: unknown) => {
        if (typeof url === 'string' && url.includes('/api/v1/ai-invoke/r1')) {
          return Promise.resolve({
            data: { status: 'finished', end_reason: 'cancelled', docs_reached: 0, last_message_received: false },
          })
        }
        if (typeof url === 'string' && url.includes('ai-invoke/providers')) {
          return Promise.resolve(PROVIDERS_RESPONSE)
        }
        return Promise.resolve(turnsPage())
      })
      postRequest.mockReset().mockResolvedValueOnce({ data: { ok: true, run_id: 'r1' } })
      const btns = wrapper.findAll('.conv-assist-btn')
      await btns[btns.length - 1].trigger('click')
      await flushPromises()

      postRequest.mockResolvedValueOnce({ data: { ok: true, run_id: 'r1', status: 'cancelling' } })
      await wrapper.find('.conv-send').trigger('click')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(2500)
      await flushPromises()

      // Never the danger "not registered" toast — the user asked for this outcome.
      expect(showToast).toHaveBeenCalledWith('The run was cancelled by the user.', 'info')
      expect(showToast).not.toHaveBeenCalledWith(expect.stringContaining('was not added'), 'danger')
      // And the button is a send button again.
      const send = wrapper.find('.conv-send')
      expect(send.classes()).not.toContain('is-stop')
      expect(send.attributes('title')).toBe('Send')
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  // A cancel can lose the race to a natural finish: the reply lands and the server never
  // stamps end_reason='cancelled'. The terminal payload decides, so the delivered reply
  // must NOT be reported as cancelled just because a cancel was in flight.
  it('reports a delivered reply as success when the cancel lost the race', async () => {
    vi.useFakeTimers()
    const wrapper = mountView()
    try {
      await flushPromises()
      getRequest.mockImplementation((url: unknown) => {
        if (typeof url === 'string' && url.includes('/api/v1/ai-invoke/r1')) {
          return Promise.resolve({
            data: { status: 'finished', end_reason: 'completed', docs_reached: 0, last_message_received: true },
          })
        }
        if (typeof url === 'string' && url.includes('ai-invoke/providers')) {
          return Promise.resolve(PROVIDERS_RESPONSE)
        }
        return Promise.resolve(turnsPage([turn(1, { speaker: 'ai', display_name: 'Opus', body: 'reply' })]))
      })
      postRequest.mockReset().mockResolvedValueOnce({ data: { ok: true, run_id: 'r1' } })
      const btns = wrapper.findAll('.conv-assist-btn')
      await btns[btns.length - 1].trigger('click')
      await flushPromises()

      // The server had already finished; cancel comes back 'finished', not 'cancelling'.
      postRequest.mockResolvedValueOnce({ data: { ok: true, run_id: 'r1', status: 'finished' } })
      await wrapper.find('.conv-send').trigger('click')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(2500)
      await flushPromises()

      // The reply arrived, so no cancellation notice and no failure toast.
      expect(showToast).not.toHaveBeenCalled()
      expect(wrapper.find('.conv-send').attributes('title')).toBe('Send')
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('blocks a new send while a chat AI call is still in flight', async () => {
    const wrapper = mountView()
    await flushPromises()
    postRequest.mockReset()
    postRequest.mockResolvedValueOnce({ data: { ok: true, run_id: 'r1' } }) // start
    const btns = wrapper.findAll('.conv-assist-btn')
    await btns[btns.length - 1].trigger('click') // manual [Call AI] → invoking
    await flushPromises()
    postRequest.mockClear()
    // Submitting while the AI call spins must be a no-op (one turn at a time).
    await wrapper.find('textarea').setValue('queued while busy')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('treats docs_reached 0 as success when the finished run appended an AI turn', async () => {
    vi.useFakeTimers()
    const wrapper = mountView()
    try {
      await flushPromises()
      getRequest.mockImplementation((url: unknown) => {
        if (typeof url === 'string' && url.includes('/api/v1/ai-invoke/r1')) {
          return Promise.resolve({ data: { status: 'finished', docs_reached: 0, last_message_received: true } })
        }
        if (typeof url === 'string' && url.includes('ai-invoke/providers')) {
          return Promise.resolve(PROVIDERS_RESPONSE)
        }
        return Promise.resolve(turnsPage([turn(1, { speaker: 'ai', display_name: 'Opus', body: 'reply' })]))
      })
      postRequest.mockReset().mockResolvedValueOnce({ data: { ok: true, run_id: 'r1' } })
      const btns = wrapper.findAll('.conv-assist-btn')
      await btns[btns.length - 1].trigger('click')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(2500)
      await flushPromises()

      expect(getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/r1')
      expect(showToast).not.toHaveBeenCalled()
      expect(wrapper.find('.conv-send').attributes('title')).toBe('Send')
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('keeps the failure toast and includes the terminal cause when no AI turn was appended', async () => {
    vi.useFakeTimers()
    const wrapper = mountView()
    try {
      await flushPromises()
      getRequest.mockImplementation((url: unknown) => {
        if (typeof url === 'string' && url.includes('/api/v1/ai-invoke/r1')) {
          return Promise.resolve({
            data: {
              status: 'finished',
              docs_reached: 0,
              last_message_received: false,
              end_reason: 'timeout',
            },
          })
        }
        if (typeof url === 'string' && url.includes('ai-invoke/providers')) {
          return Promise.resolve(PROVIDERS_RESPONSE)
        }
        return Promise.resolve(turnsPage())
      })
      postRequest.mockReset().mockResolvedValueOnce({ data: { ok: true, run_id: 'r1' } })
      const btns = wrapper.findAll('.conv-assist-btn')
      await btns[btns.length - 1].trigger('click')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(2500)
      await flushPromises()

      expect(showToast).toHaveBeenCalledWith(
        'The AI reply was not added to the chat: The run exceeded its time limit and was terminated.',
        'danger',
      )
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })
})

describe('ConversationView draft persistence', () => {
  it('restores the exact draft after unmount and remount', async () => {
    const text = '  first line\nsecond line  '
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue(text)
    expect(localStorage.getItem(draftKey())).toBe(text)

    wrapper.unmount()
    const restored = mountView()
    await flushPromises()
    expect((restored.find('textarea').element as HTMLTextAreaElement).value).toBe(text)
  })

  it('keeps drafts isolated by document and restores them when docId changes', async () => {
    localStorage.setItem(draftKey(DOC_ID), 'draft A')
    localStorage.setItem(draftKey(OTHER_DOC_ID), 'draft B')
    const wrapper = mountView(DOC_ID)
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('draft A')

    await wrapper.setProps({ docId: OTHER_DOC_ID })
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('draft B')
  })

  it('uses the signed-in username to isolate the storage key', async () => {
    const payload = btoa(JSON.stringify({ username: 'alice' }))
    window.__accessToken__ = 'header.' + payload + '.signature'
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('alice draft')
    expect(localStorage.getItem(draftKey(DOC_ID, 'alice'))).toBe('alice draft')
    expect(localStorage.getItem(draftKey())).toBeNull()
  })

  it('removes the stored draft after a successful send', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('send me')
    expect(localStorage.getItem(draftKey())).toBe('send me')

    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('')
    expect(localStorage.getItem(draftKey())).toBeNull()
  })

  it('keeps the stored draft when send fails', async () => {
    postRequest.mockReset().mockRejectedValueOnce(new Error('boom'))
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('retry me')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('retry me')
    expect(localStorage.getItem(draftKey())).toBe('retry me')
  })

  it('removes the storage entry when the input is cleared', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('temporary')
    await wrapper.find('textarea').setValue('')
    expect(localStorage.getItem(draftKey())).toBeNull()
  })

  it('continues rendering and sending when localStorage throws', async () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked')
    })
    const wrapper = mountView()
    await flushPromises()
    getItem.mockRestore()

    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota')
    })
    await wrapper.find('textarea').setValue('still sends')
    setItem.mockRestore()
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/documents/flowgate.default.0085.0009-CH/conversation/turn',
      expect.objectContaining({ body: 'still sends' }),
    )
  })
})

// 0251 B0001 (NR0003 §5, B안): a chat AI call's spinner and its poll loop live only in
// this component, so every remount — opening the full view, switching tabs, F5 — used to
// hand the user an idle-looking chat while the call was still running server-side, and
// lost the "registered nothing" notice with it. On mount the view re-discovers its own
// still-running run and re-attaches to it.
describe('ConversationView running-call recovery on mount', () => {
  function withActiveRun(payload: Record<string, unknown>) {
    getRequest.mockImplementation((url: unknown) => {
      if (typeof url === 'string' && url.includes('ai-invoke/providers')) {
        return Promise.resolve(PROVIDERS_RESPONSE)
      }
      if (typeof url === 'string' && url.includes('ai-invoke/active')) {
        return Promise.resolve({ data: payload })
      }
      return Promise.resolve(turnsPage())
    })
  }

  it('restores the STOP button for a run that is still running for THIS chat', async () => {
    withActiveRun({ ok: true, active: true, run_id: 'r7', status: 'running', doc_ref: DOC_ID })
    const wrapper = mountView()
    await flushPromises()

    expect(getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/active', {
      group_id: 'flowgate.default.0085',
    })
    // Adopted: the poll loop holds invoking=true (its first tick is behind a 2.5s timer),
    // so the send button shows the in-flight call exactly as if this instance started it —
    // including the ability to stop a run this instance never started (0264 R0001).
    const send = wrapper.find('.conv-send')
    expect(send.classes()).toContain('is-stop')
    expect(send.attributes('disabled')).toBeUndefined()
    expect(send.attributes('title')).toBe('Cancel run')

    postRequest.mockClear()
    postRequest.mockResolvedValueOnce({ data: { ok: true, run_id: 'r7', status: 'cancelling' } })
    await send.trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/r7/cancel', {})
  })

  it('stays idle when the group\'s active run belongs to another document', async () => {
    withActiveRun({ ok: true, active: true, run_id: 'r7', status: 'running', doc_ref: OTHER_DOC_ID })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.conv-send').attributes('title')).toBe('Send')
  })

  it('stays idle when the run has already finished', async () => {
    withActiveRun({ ok: true, active: true, run_id: 'r7', status: 'finished', doc_ref: DOC_ID })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.conv-send').attributes('title')).toBe('Send')
  })

  it('stays idle when no run is active', async () => {
    withActiveRun({ ok: true, active: false })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.conv-send').attributes('title')).toBe('Send')
  })

  it('keeps the chat usable when the discovery request fails', async () => {
    getRequest.mockImplementation((url: unknown) => {
      if (typeof url === 'string' && url.includes('ai-invoke/providers')) {
        return Promise.resolve(PROVIDERS_RESPONSE)
      }
      if (typeof url === 'string' && url.includes('ai-invoke/active')) {
        return Promise.reject({ response: { status: 500 } })
      }
      return Promise.resolve(turnsPage())
    })
    const wrapper = mountView()
    await flushPromises()
    // Best effort: an idle surface is the status quo, and never an error toast.
    expect(wrapper.find('.conv-send').attributes('title')).toBe('Send')
    expect(showToast).not.toHaveBeenCalled()
  })
})
// 0293 R0001, carried onto the turn contract by 0351 T2: the model name now arrives as
// the turn's own `display_name` field instead of being encoded into a markdown header.
// It stays a BADGE — identity is participant_key, so a renamed model is the same speaker.
describe('ConversationView provider badge', () => {
  function withRows(rows: unknown[]) {
    getRequest.mockImplementation((url: unknown) => {
      if (typeof url === 'string' && url.includes('ai-invoke/providers')) {
        return Promise.resolve(PROVIDERS_RESPONSE)
      }
      return Promise.resolve(turnsPage(rows))
    })
  }

  it('renders the model name recorded on the turn', async () => {
    withRows([turn(1, { speaker: 'ai', display_name: 'claude-opus-4-8', body: 'reply' })])
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.conv-provider').text()).toBe('claude-opus-4-8')
    // Still an AI bubble, and the reply text is untouched.
    expect(wrapper.find('.conv-row--ai').exists()).toBe(true)
    expect(wrapper.find('.conv-body').text()).toBe('reply')
  })

  it('draws no badge when the turn records no model name', async () => {
    // Absence of information, not a warning state — a worker that does not know its own
    // name simply omits the field.
    withRows([turn(1, { speaker: 'ai', display_name: null, body: 'reply' })])
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.conv-provider').exists()).toBe(false)
    expect(wrapper.find('.conv-row--ai').exists()).toBe(true)
  })

  // NR0004 발견 2: the failure this feature can cause is silent. A provider-bearing label
  // that does not normalize to 'ai' still RENDERS as an AI bubble, but pollRun counts AI
  // turns to decide whether the reply landed — so a delivered reply would be reported as
  // "no reply" on every single chat AI call.
  it('counts a provider-bearing turn as an AI turn when judging a finished run', async () => {
    vi.useFakeTimers()
    const wrapper = mountView()
    try {
      await flushPromises()
      getRequest.mockImplementation((url: unknown) => {
        if (typeof url === 'string' && url.includes('/api/v1/ai-invoke/r1')) {
          return Promise.resolve({ data: { status: 'finished', docs_reached: 0, last_message_received: true } })
        }
        if (typeof url === 'string' && url.includes('ai-invoke/providers')) {
          return Promise.resolve(PROVIDERS_RESPONSE)
        }
        return Promise.resolve(turnsPage([turn(1, { speaker: 'ai', display_name: 'claude-opus-4-8', body: 'reply' })]))
      })
      postRequest.mockReset().mockResolvedValueOnce({ data: { ok: true, run_id: 'r1' } })
      const btns = wrapper.findAll('.conv-assist-btn')
      await btns[btns.length - 1].trigger('click')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(2500)
      await flushPromises()

      expect(showToast).not.toHaveBeenCalled()
      expect(wrapper.find('.conv-provider').text()).toBe('claude-opus-4-8')
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })
})
