import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02.0135 T#3 — a new chat whose create answer was lost. On stg POST /conversations answered
// only after ~2 minutes: the GUI gave up at 30 s ("새 채팅을 만들지 못했습니다") while the server still made the
// chat, and the next try made a second one (NR 3.2: two CH, no turn). The panel now sends one create intent
// (`client_request_id`) for every try of a draft; the server (flowgate/conversation/chats.py) answers the chat an
// earlier try of that intent made. This file drives the real panel and its default HTTP source against an
// in-memory server with that contract, in the two lost-answer orders the review reproduced:
//   1. timeout -> the list re-read shows nothing -> the first create commits late -> the user tries again
//   2. the create commits, only its answer is lost -> the user tries again
// Each must end with ONE chat and the first message stored ONCE.
type Call = { method: string; path: string; body?: unknown; params?: unknown }

const transport = vi.hoisted(() => ({
  calls: [] as Array<{ method: string; path: string; body?: unknown; params?: unknown }>,
  handler: null as null | ((call: { method: string; path: string; body?: unknown; params?: unknown }) => unknown),
}))

vi.mock('@shared/api', () => {
  const send = async (method: string, path: string, payload: { body?: unknown; params?: unknown }) => {
    const call = { method, path, ...payload }
    transport.calls.push(call)
    return { data: await transport.handler!(call) }
  }
  return {
    getRequest: (path: string, params?: unknown) => send('GET', path, { params }),
    postRequest: (path: string, body?: unknown) => send('POST', path, { body }),
    patchRequest: (path: string, body?: unknown) => send('PATCH', path, { body }),
    extractApiErrorMessage: (_e: unknown, fallback: string) => fallback,
  }
})

const clipboard = vi.hoisted(() => ({ copyToClipboard: vi.fn(async () => true) }))
vi.mock('@/core/interaction/clipboard', () => clipboard)

import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'

registerAppMessages(i18n)

const PROJECT = 'flowgate'
const GROUP = 'flowgate.v02test.0009'
const OTHER = 'flowgate.v02test.0010'

/** A request that got no answer (axios timeout): no `response`, so the GUI sees status null. */
function noAnswer(): Error {
  return Object.assign(new Error('timeout of 30000ms exceeded'), { code: 'ECONNABORTED' })
}

type CreateMode = 'answer' | 'lost_then_commit_late' | 'commit_then_lose_answer'

/** The Conversation slice of the server, with the 0135 create intent contract of POST /conversations. */
function createServer() {
  const server = {
    chats: [] as Array<{ doc_id: string; title: string; group_id: string; type_code: 'CH'; updated_at: string; intent?: string }>,
    turns: {} as Record<string, Array<{ seq: number; speaker: string; body: string; created_at: string }>>,
    modes: [] as CreateMode[],
    /** Creates whose answer was lost before they were stored: committed by commitLate(). */
    running: [] as Array<() => void>,
    nextNo: 2,
    commitLate() {
      for (const commit of server.running.splice(0)) commit()
    },
  }
  const docPath = (path: string) => decodeURIComponent(path.split('/')[4])
  const make = (body: Record<string, unknown>) => {
    const doc_id = `${body.group_id}.00${String(server.nextNo++).padStart(2, '0')}-CH`
    const chat = { doc_id, title: String(body.title), group_id: String(body.group_id), type_code: 'CH' as const,
      updated_at: `2026-11-05T09:0${server.nextNo}:00Z`, intent: body.client_request_id as string | undefined }
    server.chats.unshift(chat)
    server.turns[doc_id] = []
    return chat
  }
  transport.handler = (call: Call) => {
    const { method, path } = call
    const body = (call.body ?? {}) as Record<string, unknown>
    if (method === 'GET' && path === '/api/v1/documents') {
      const groupId = (call.params as { group_id?: string }).group_id
      return server.chats.filter((c) => c.group_id === groupId).map(({ intent: _intent, ...c }) => c)
    }
    if (method === 'GET' && path.endsWith('/conversation/turns')) {
      const all = server.turns[docPath(path)] ?? []
      return { turns: all, participants: [], me: null, head_seq: all.at(-1)?.seq ?? 0, next_after_seq: null, prev_before_seq: null }
    }
    if (method === 'POST' && path.endsWith('/conversation/read')) return { me: { participant_key: 'u', last_read_seq: body.last_read_seq } }
    if (method === 'GET' && path === '/api/v1/ai-invoke/providers') return { providers: [], default_provider_id: null }
    if (method === 'GET' && path === '/api/v1/me/chat-settings') return { settings: { send_action: 'none' }, domain: {} }
    if (method === 'GET' && path === '/api/v1/ai-invoke/active') return { active: false }
    if (method === 'GET' && /^\/api\/v1\/chat-activity\/[^/]+$/.test(path)) return { ok: true, doc_id: path.split('/').pop(), commands: [], changes: [] }
    if (method === 'POST' && path === '/api/v1/conversations') {
      // The intent contract (server/flowgate/conversation/chats.py): the chat this intent already made is answered.
      const made = body.client_request_id ? server.chats.find((c) => c.intent === body.client_request_id && c.group_id === body.group_id) : undefined
      if (made) return { ok: true, doc_id: made.doc_id, title: made.title, type_code: 'CH', status: 'approved', updated_at: made.updated_at, replayed: true }
      const mode = server.modes.shift() ?? 'answer'
      if (mode === 'lost_then_commit_late') {
        server.running.push(() => void make(body))
        throw noAnswer()
      }
      const chat = make(body)
      if (mode === 'commit_then_lose_answer') throw noAnswer()
      return { ok: true, doc_id: chat.doc_id, title: chat.title, type_code: 'CH', status: 'approved', updated_at: chat.updated_at }
    }
    if (method === 'POST' && path.endsWith('/conversation/turn')) {
      const list = (server.turns[docPath(path)] ??= [])
      const turn = { seq: list.length + 1, speaker: 'user', body: String(body.body), created_at: '2026-11-05T09:10:00Z' }
      list.push(turn)
      return { turn, head_seq: turn.seq }
    }
    throw Object.assign(new Error('no route'), { response: { status: 404, data: { ok: false, error: { code: 'no_route' } } } })
  }
  return server
}

let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  transport.calls.length = 0
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

async function mountPanel(groupId = GROUP) {
  wrapper = mount(ConversationPanel, {
    props: { projectId: PROJECT, groupId },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return wrapper
}

const find = (id: string) => wrapper!.find(`[data-test="${id}"]`)
const findAll = (id: string) => wrapper!.findAll(`[data-test="${id}"]`)
const creates = () => transport.calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/conversations')
const intentOf = (call: Call) => (call.body as { client_request_id?: string }).client_request_id

async function submit(text: string) {
  await find('conv-input').setValue(text)
  await find('conv-composer').trigger('submit')
  await flushPromises()
}

describe('a lost create answer never makes a second chat (flowgate.v02.0135 T#3)', () => {
  it('timeout -> empty list re-read -> the first create commits late -> retry: one chat, the first message once', async () => {
    const server = createServer()
    server.modes.push('lost_then_commit_late')
    await mountPanel()

    await submit('첫 메시지')
    expect(find('conv-notice').exists()).toBe(true) // the try failed on screen
    // the list read after the timeout: the server has not stored the chat yet
    expect(server.chats).toHaveLength(0)
    expect(transport.calls.filter((c) => c.method === 'GET' && c.path === '/api/v1/documents').length).toBeGreaterThanOrEqual(2)

    server.commitLate() // the first create commits minutes later (stg: +116 s)
    expect(server.chats.map((c) => c.doc_id)).toEqual([`${GROUP}.0002-CH`])

    await submit('첫 메시지') // the user tries again from the same draft
    const [first, retry] = creates()
    expect(intentOf(first)).toMatch(/^chat-/)
    expect(intentOf(retry)).toBe(intentOf(first))
    expect(server.chats.map((c) => c.doc_id)).toEqual([`${GROUP}.0002-CH`]) // no second chat
    expect(server.turns[`${GROUP}.0002-CH`].map((t) => t.body)).toEqual(['첫 메시지']) // stored once
    expect(findAll('tl-message').map((m) => m.find('.tl-text').text())).toEqual(['첫 메시지'])
    expect(find('conv-notice').exists()).toBe(false)
  })

  it('the create commits but only its answer is lost -> retry: one chat, the first message once', async () => {
    const server = createServer()
    server.modes.push('commit_then_lose_answer')
    await mountPanel()

    await submit('첫 메시지')
    expect(server.chats).toHaveLength(1)
    expect(server.turns[`${GROUP}.0002-CH`]).toEqual([]) // the message was not sent to a chat the GUI did not get

    await submit('첫 메시지')
    expect(creates()).toHaveLength(2)
    expect(intentOf(creates()[1])).toBe(intentOf(creates()[0]))
    expect(server.chats).toHaveLength(1)
    expect(server.turns[`${GROUP}.0002-CH`].map((t) => t.body)).toEqual(['첫 메시지'])

    // reload (F5): the list shows that one chat with its one message
    wrapper!.unmount()
    await mountPanel()
    expect(server.chats).toHaveLength(1)
    expect(findAll('tl-message').map((m) => m.find('.tl-text').text())).toEqual(['첫 메시지'])
  })

  it('another draft or another Flow is another intent; a made chat ends its intent', async () => {
    const server = createServer()
    server.modes.push('commit_then_lose_answer')
    await mountPanel()
    await submit('첫 메시지')
    const lost = intentOf(creates()[0])

    await find('conv-new-btn').trigger('click') // [+]: a new draft is a new intent
    await flushPromises()
    await submit('다른 대화')
    expect(intentOf(creates()[1])).not.toBe(lost)
    expect(server.chats).toHaveLength(2)

    await find('conv-new-btn').trigger('click')
    await flushPromises()
    await submit('세 번째') // the previous intent made its chat: not reused
    expect(new Set(creates().map(intentOf)).size).toBe(3)

    await wrapper!.setProps({ groupId: OTHER })
    await flushPromises()
    await submit('다른 플로우')
    expect(new Set(creates().map(intentOf)).size).toBe(4)
    expect(server.chats.filter((c) => c.group_id === OTHER)).toHaveLength(1)
  })
})
