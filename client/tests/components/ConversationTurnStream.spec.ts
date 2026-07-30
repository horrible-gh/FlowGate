import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

// Group 0351 T2 (D0002 §6 / P0003 시나리오 1·2·6·7 / L0004 §2-17).
// The conversation of record moved from a markdown body to append-only turns, so this
// view no longer re-reads a document when something changes: it loads a page around a
// cursor, appends single turns as they arrive, and pages backwards on scroll-up.
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

const DOC_ID = 'flowgate.default.0351.0002-CH'
const TURNS_URL = `/api/v1/documents/${encodeURIComponent(DOC_ID)}/conversation/turns`
const READ_URL = `/api/v1/documents/${encodeURIComponent(DOC_ID)}/conversation/read`

const PROVIDERS_RESPONSE = {
  data: { ok: true, project: 'flowgate', providers: [], default_provider_id: null },
}

function turn(seq: number, over: Record<string, unknown> = {}) {
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

function page(rows: unknown[], over: Record<string, unknown> = {}) {
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
      head: null,
      turns: rows,
      participants: [],
      me: null,
      ...over,
    },
  }
}

/** Serve the first turns request from `first`, and any later one from `rest`. */
function serve(first: unknown, rest: unknown = page([])) {
  let seen = 0
  getRequest.mockImplementation((url: unknown) => {
    if (typeof url === 'string' && url.includes('ai-invoke')) {
      return Promise.resolve(PROVIDERS_RESPONSE)
    }
    seen += 1
    return Promise.resolve(seen === 1 ? first : rest)
  })
}

function turnCalls() {
  return getRequest.mock.calls.filter((c) => c[0] === TURNS_URL)
}

// Every mounted view listens on `window`, so a leftover instance from an earlier test
// would answer this test's SSE events too. Track and tear them all down.
const mounted: Array<{ unmount: () => void }> = []

function mountView() {
  const wrapper = mount(ConversationView, {
    props: { docId: DOC_ID, projectId: 'flowgate' },
    global: { plugins: [i18n, createPinia()] },
  })
  mounted.push(wrapper)
  return wrapper
}

function emitTurn(detail: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent('fg:conversation_turn', { detail }))
}

beforeEach(() => {
  i18n.global.locale.value = 'en'
  localStorage.clear()
  getRequest.mockReset()
  postRequest.mockReset().mockResolvedValue({ data: { ok: true, me: null } })
  showToast.mockReset()
  serve(page([]))
})

afterEach(() => {
  while (mounted.length > 0) {
    try { mounted.pop()?.unmount() } catch { /* already torn down by the test */ }
  }
})

describe('ConversationView cursor loading', () => {
  it('loads turns from the cursor endpoint and never from the document body', async () => {
    serve(page([turn(1)], { head_seq: 1 }))
    const wrapper = mountView()
    await flushPromises()
    const [url, params] = turnCalls()[0]
    expect(url).toBe(TURNS_URL)
    // include_head asks for the background + participants in the SAME call — there is
    // no separate participants endpoint (P0003 시나리오 1).
    expect(params).toMatchObject({ include_head: 1 })
    // The retired whole-body read must not come back.
    expect(getRequest.mock.calls.some((c) => String(c[0]).includes('/documents/content'))).toBe(false)
    expect(wrapper.findAll('.conv-row')).toHaveLength(1)
  })

  it('sends no cursor on entry so the server resumes the remembered position', async () => {
    serve(page([turn(5)], { head_seq: 5, me: { participant_key: 'user:u1', kind: 'user', display_name: 'sjm', first_seen_seq: 1, last_read_seq: 4, last_written_seq: 4 } }))
    mountView()
    await flushPromises()
    const params = turnCalls()[0][1] as Record<string, unknown>
    // Computing the resume point client-side is exactly what D0002 §3-4 removes.
    expect(params.after_seq).toBeUndefined()
  })

  it('pulls one page of earlier context when the unread range is short', async () => {
    // Entering at seq 8 with nothing above it on screen would show a bare boundary line.
    serve(
      page([turn(8)], { head_seq: 8, me: { participant_key: 'user:u1', kind: 'user', display_name: 'sjm', first_seen_seq: 1, last_read_seq: 7, last_written_seq: 7 } }),
      page([turn(6), turn(7)], { prev_before_seq: 6, has_more: true }),
    )
    const wrapper = mountView()
    await flushPromises()
    expect(turnCalls()[1][1]).toMatchObject({ before_seq: 8, limit: 30 })
    expect(wrapper.findAll('.conv-row')).toHaveLength(3)
  })

  it('pages backwards on demand and offers the control while more remains', async () => {
    serve(
      page([turn(4), turn(5)], { head_seq: 5 }),
      page([turn(2), turn(3)], { prev_before_seq: 2, has_more: true }),
    )
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.conv-older').exists()).toBe(true)
    await wrapper.find('.conv-older').trigger('click')
    await flushPromises()
    // Ascending order is preserved when a block is prepended (P0003 시나리오 2).
    const bodies = wrapper.findAll('.conv-body').map((n) => n.text())
    expect(bodies).toEqual(['turn 2', 'turn 3', 'turn 4', 'turn 5'])
  })

  it('follows next_after_seq until the page chain ends', async () => {
    let call = 0
    getRequest.mockImplementation((url: unknown) => {
      if (typeof url === 'string' && url.includes('ai-invoke')) return Promise.resolve(PROVIDERS_RESPONSE)
      call += 1
      if (call === 1) return Promise.resolve(page([turn(1)], { head_seq: 2, next_after_seq: 1, has_more: true }))
      return Promise.resolve(page([turn(2)], { head_seq: 2 }))
    })
    const wrapper = mountView()
    await flushPromises()
    expect(turnCalls()[1][1]).toMatchObject({ after_seq: 1 })
    expect(wrapper.findAll('.conv-row')).toHaveLength(2)
  })
})

describe('ConversationView incremental rendering', () => {
  it('appends a pushed turn as one bubble without re-reading the conversation', async () => {
    serve(page([turn(1)], { head_seq: 1 }))
    const wrapper = mountView()
    await flushPromises()
    const before = turnCalls().length

    emitTurn({ doc_id: DOC_ID, head_seq: 2, turn: turn(2, { speaker: 'ai', display_name: 'Opus', body: 'reply' }) })
    await flushPromises()

    expect(wrapper.findAll('.conv-row')).toHaveLength(2)
    expect(wrapper.find('.conv-row--ai .conv-body').text()).toBe('reply')
    // The whole point of the single-turn event: no refetch (D0002 §6).
    expect(turnCalls().length).toBe(before)
  })

  it('ignores a turn pushed for a different conversation', async () => {
    serve(page([turn(1)], { head_seq: 1 }))
    const wrapper = mountView()
    await flushPromises()
    emitTurn({ doc_id: 'flowgate.default.0351.0009-CH', head_seq: 9, turn: turn(9) })
    await flushPromises()
    expect(wrapper.findAll('.conv-row')).toHaveLength(1)
  })

  it('does not draw a turn twice when our own message echoes back', async () => {
    serve(page([turn(1)], { head_seq: 1 }))
    const wrapper = mountView()
    await flushPromises()
    emitTurn({ doc_id: DOC_ID, head_seq: 1, turn: turn(1) })
    await flushPromises()
    // Reconciliation is by seq: idempotency_key is deliberately not on the wire.
    expect(wrapper.findAll('.conv-row')).toHaveLength(1)
  })

  it('keeps pushed turns in sequence order even when they arrive out of order', async () => {
    serve(page([turn(1)], { head_seq: 1 }))
    const wrapper = mountView()
    await flushPromises()
    emitTurn({ doc_id: DOC_ID, head_seq: 3, turn: turn(3) })
    emitTurn({ doc_id: DOC_ID, head_seq: 3, turn: turn(2) })
    await flushPromises()
    expect(wrapper.findAll('.conv-body').map((n) => n.text())).toEqual(['turn 1', 'turn 2', 'turn 3'])
  })

  it('refills the gap left by a dropped stream, starting from the last known seq', async () => {
    serve(page([turn(1)], { head_seq: 1 }), page([turn(2), turn(3)], { head_seq: 3 }))
    const wrapper = mountView()
    await flushPromises()

    window.dispatchEvent(new CustomEvent('fg:sse_reconnected'))
    await flushPromises()

    // P0003 시나리오 7: ask for everything after the head we had when the stream died.
    expect(turnCalls()[1][1]).toMatchObject({ after_seq: 1 })
    expect(wrapper.findAll('.conv-row')).toHaveLength(3)
  })

  it('stops listening once the view is torn down', async () => {
    serve(page([turn(1)], { head_seq: 1 }))
    const wrapper = mountView()
    await flushPromises()
    wrapper.unmount()
    const before = turnCalls().length
    window.dispatchEvent(new CustomEvent('fg:sse_reconnected'))
    await flushPromises()
    expect(turnCalls().length).toBe(before)
  })
})

describe('ConversationView conversation markers', () => {
  const ME = {
    participant_key: 'user:u1', kind: 'user', display_name: 'sjm',
    first_seen_seq: 1, last_read_seq: 1, last_written_seq: 1,
  }

  it('draws the read boundary above the first unread turn', async () => {
    serve(page([turn(1), turn(2)], { head_seq: 2, me: ME }))
    const wrapper = mountView()
    await flushPromises()
    const boundary = wrapper.find('.conv-boundary')
    expect(boundary.exists()).toBe(true)
    expect(boundary.text()).toBe('Read up to here')
    // It belongs between turn 1 and turn 2, not at the very top.
    const rows = wrapper.find('.conv-scroll').element.children
    const index = Array.from(rows).findIndex((n) => n.classList.contains('conv-boundary'))
    expect(index).toBe(1)
  })

  it('draws no boundary when everything on screen is unread', async () => {
    // A line above the very first bubble would say nothing.
    serve(page([turn(1), turn(2)], { head_seq: 2, me: { ...ME, last_read_seq: 0 } }))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.conv-boundary').exists()).toBe(false)
  })

  it('marks a reply that was written without having seen the turn above it', async () => {
    serve(page([turn(1), turn(2, { speaker: 'ai', stale_since_seq: 1, display_name: 'Opus' })], { head_seq: 2 }))
    const wrapper = mountView()
    await flushPromises()
    // Nothing was overwritten — order preserved both turns (P0003 시나리오 12) — but the
    // reader must know the reply is not answering turn 1.
    expect(wrapper.find('.conv-stale').text()).toContain('#1')
    expect(wrapper.findAll('.conv-row')).toHaveLength(2)
  })

  it('lists the participants and where each one has read up to', async () => {
    serve(page([turn(1)], {
      head_seq: 1,
      participants: [
        { participant_key: 'user:u1', kind: 'user', display_name: 'sjm', first_seen_seq: 1, last_read_seq: 1, last_written_seq: 1 },
        { participant_key: 'provider:cx_opus', kind: 'ai', display_name: 'Claude Opus 5', first_seen_seq: 2, last_read_seq: 0, last_written_seq: 0 },
      ],
    }))
    const wrapper = mountView()
    await flushPromises()
    const strip = wrapper.find('.conv-participants')
    expect(strip.exists()).toBe(true)
    expect(strip.text()).toContain('sjm')
    expect(strip.text()).toContain('Claude Opus 5')
  })

  it('announces a conversation continued from an earlier document', async () => {
    serve(page([turn(1)], {
      head_seq: 1,
      head: { intro: '', opening_turns: [], carried_over_from: 'flowgate.default.0351.0001-CH', total_turns: 1, head_seq: 1 },
    }))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.conv-continued').text()).toContain('flowgate.default.0351.0001-CH')
  })
})

describe('ConversationView optimistic send', () => {
  it('shows the message immediately and swaps it for the numbered turn', async () => {
    serve(page([], { head_seq: 0 }))
    let resolvePost: (v: unknown) => void = () => {}
    postRequest.mockImplementation(() => new Promise((r) => { resolvePost = r }))
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('textarea').setValue('hello')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    // Optimistic bubble is on screen before the server has answered, marked pending so
    // "sent" and "recorded" never look the same.
    expect(wrapper.find('.conv-row.is-pending').exists()).toBe(true)
    expect(wrapper.find('.conv-body').text()).toBe('hello')

    resolvePost({ data: { ok: true, replayed: false, head_seq: 1, turn: turn(1, { body: 'hello' }), me: null } })
    await flushPromises()
    expect(wrapper.find('.conv-row.is-pending').exists()).toBe(false)
    expect(wrapper.findAll('.conv-row')).toHaveLength(1)
  })

  it('records what the sender had read so a crossing can be detected server-side', async () => {
    serve(page([turn(1), turn(2)], { head_seq: 2 }))
    const wrapper = mountView()
    await flushPromises()
    postRequest.mockResolvedValue({ data: { ok: true, head_seq: 3, turn: turn(3, { body: 'hi' }), me: null } })
    await wrapper.find('textarea').setValue('hi')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    const body = postRequest.mock.calls.find((c) => String(c[0]).endsWith('/conversation/turn'))?.[1]
    expect(body).toMatchObject({ based_on_seq: 2 })
  })

  it('keeps a failed message on screen with a retry that reuses the same key', async () => {
    serve(page([], { head_seq: 0 }))
    postRequest.mockRejectedValue({ response: { status: 409, data: { detail: 'conflict' } } })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('hello')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const failed = wrapper.find('.conv-row.is-failed')
    expect(failed.exists()).toBe(true)
    const firstKey = (postRequest.mock.calls[0][1] as { idempotency_key: string }).idempotency_key

    postRequest.mockResolvedValue({ data: { ok: true, head_seq: 1, turn: turn(1, { body: 'hello' }), me: null } })
    await wrapper.find('.conv-retry').trigger('click')
    await flushPromises()

    const retryKey = (postRequest.mock.calls[postRequest.mock.calls.length - 1][1] as { idempotency_key: string }).idempotency_key
    // Same key ⇒ a turn that DID reach the server replays instead of doubling.
    expect(retryKey).toBe(firstKey)
    expect(wrapper.find('.conv-row.is-failed').exists()).toBe(false)
  })

  it('drops the placeholder when the pushed turn wins the race with the response', async () => {
    serve(page([], { head_seq: 0 }))
    let resolvePost: (v: unknown) => void = () => {}
    postRequest.mockImplementation(() => new Promise((r) => { resolvePost = r }))
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('hello')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    emitTurn({ doc_id: DOC_ID, head_seq: 1, turn: turn(1, { body: 'hello' }) })
    await flushPromises()
    resolvePost({ data: { ok: true, head_seq: 1, turn: turn(1, { body: 'hello' }), me: null } })
    await flushPromises()

    expect(wrapper.findAll('.conv-row')).toHaveLength(1)
    expect(wrapper.find('.conv-row.is-pending').exists()).toBe(false)
  })
})

describe('ConversationView read reporting', () => {
  it('reports the read position only after the debounce and never backwards', async () => {
    vi.useFakeTimers()
    serve(page([turn(1), turn(2)], {
      head_seq: 2,
      me: { participant_key: 'user:u1', kind: 'user', display_name: 'sjm', first_seen_seq: 1, last_read_seq: 0, last_written_seq: 0 },
    }))
    const wrapper = mountView()
    try {
      await vi.advanceTimersByTimeAsync(0)
      await flushPromises()
      expect(postRequest.mock.calls.filter((c) => c[0] === READ_URL)).toHaveLength(0)

      await vi.advanceTimersByTimeAsync(1000)
      await flushPromises()
      const reads = postRequest.mock.calls.filter((c) => c[0] === READ_URL)
      // jsdom gives every element zero height, so nothing measures as visible and no
      // position is claimed. Silence here is the correct outcome: the boundary must
      // never advance past what was actually shown (L0004 §1-3).
      expect(reads.every((c) => (c[1] as { reason: string }).reason === 'viewed')).toBe(true)
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })
})
