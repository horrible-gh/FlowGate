import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02.0032 T#2 integration — the Conversation panel with its DEFAULT HTTP source (no
// injected source) against an in-memory server behind the shared transport. It walks the
// production path of D0006 §3.9.2 / §5.5 / §6: a pending command shows up within one polling
// tick, the decision goes out and the same run carries on, the run change and its stored file
// diff arrive after the run, a reopen restores the server state, and [새 채팅] makes a
// standalone CH through POST /conversations during the run — never through next-empty.
type Call = { method: 'GET' | 'POST' | 'PATCH'; path: string; body?: unknown; params?: unknown }

const transport = vi.hoisted(() => ({
  calls: [] as Array<{ method: string; path: string; body?: unknown; params?: unknown }>,
  handler: null as null | ((call: { method: string; path: string; body?: unknown; params?: unknown }) => unknown),
}))

vi.mock('@shared/api', () => {
  const send = async (method: string, path: string, payload: { body?: unknown; params?: unknown }) => {
    const call = { method, path, ...payload }
    transport.calls.push(call)
    return { data: transport.handler!(call) }
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
import { RUN_POLL_MS } from '@/features/conversation/conversationSession'

registerAppMessages(i18n)

const GROUP = 'flowgate.v02.0032'
const CHAT = `${GROUP}.0011-CH`

interface ServerTurn {
  seq: number
  speaker: string
  body: string
  created_at: string
}

function httpError(status: number, code: string) {
  return Object.assign(new Error(code), { response: { status, data: { ok: false, error: { code, message: code } } } })
}

/** The slice of the FlowGate server the Conversation panel talks to. */
function createServer() {
  const server = {
    chats: [{ doc_id: CHAT, title: '빌드 확인', type_code: 'CH', updated_at: '2026-10-06T08:00:00Z' }] as Array<Record<string, unknown>>,
    turns: {
      [CHAT]: [
        { seq: 1, speaker: 'user', body: '빌드 돌려 줘', created_at: '2026-10-06T08:01:00Z' },
      ],
    } as Record<string, ServerTurn[]>,
    commands: [] as Array<Record<string, unknown>>,
    changes: [] as Array<Record<string, unknown>>,
    run: null as null | { id: string; status: 'running' | 'finished' },
    nextChatNo: 12,
  }
  const docPath = (path: string) => decodeURIComponent(path.split('/')[4])
  transport.handler = (call) => {
    const { method, path } = call
    const body = (call.body ?? {}) as Record<string, unknown>
    if (method === 'GET' && path === '/api/v1/documents') return server.chats
    if (method === 'GET' && path.endsWith('/conversation/turns')) {
      const all = server.turns[docPath(path)] ?? []
      const after = (call.params as { after_seq?: number } | undefined)?.after_seq
      const list = after != null ? all.filter((t) => t.seq > after) : all
      return { turns: list, participants: [], me: null, head_seq: all.at(-1)?.seq ?? 0, next_after_seq: null, prev_before_seq: null }
    }
    if (method === 'POST' && path.endsWith('/conversation/read')) return { me: { participant_key: 'u', last_read_seq: body.last_read_seq } }
    if (method === 'GET' && path === '/api/v1/ai-invoke/providers') return { providers: [{ id: 'opus', name: 'Opus' }], default_provider_id: 'opus' }
    if (method === 'GET' && path === '/api/v1/me/chat-settings') return { settings: { send_action: 'none' }, domain: {} }
    if (method === 'GET' && path === '/api/v1/ai-invoke/active') {
      return server.run && server.run.status === 'running' ? { active: true, run_id: server.run.id, status: 'running', doc_ref: CHAT } : { active: false }
    }
    if (method === 'POST' && path === '/api/v1/ai-invoke/start') {
      server.run = { id: 'run-1', status: 'running' }
      return { run_id: 'run-1' }
    }
    if (method === 'GET' && path.startsWith('/api/v1/ai-invoke/run-')) return { status: server.run?.status ?? 'finished', end_reason: null }
    if (method === 'GET' && /^\/api\/v1\/chat-activity\/[^/]+$/.test(path)) {
      const doc = decodeURIComponent(path.split('/').pop()!)
      return {
        ok: true,
        doc_id: doc,
        commands: server.commands.filter((c) => c.doc_id === doc),
        changes: server.changes.filter((c) => c.doc_id === doc),
      }
    }
    if (method === 'GET' && path.endsWith('/diff')) {
      return {
        ok: true,
        data: {
          run_id: 'run-1',
          path: (call.params as { path: string }).path,
          status: 'M',
          old: { exists: true, binary: false, truncated: false, size: 9, content: 'x\nbuild: 1\n' },
          new: { exists: true, binary: false, truncated: false, size: 9, content: 'x\nbuild: 2\n' },
        },
      }
    }
    const decision = path.match(/^\/api\/v1\/chat-commands\/([^/]+)\/decision$/)
    if (method === 'POST' && decision) {
      const command = server.commands.find((c) => c.request_id === decision[1])
      if (!command) throw httpError(404, 'chat_command_not_found')
      if (command.status !== 'pending_approval') throw httpError(409, 'chat_command_already_decided')
      Object.assign(command, body.decision === 'approve' ? { status: 'approved' } : { status: 'rejected', rejected_by: 'user' })
      return { ok: true, request: { ...command } }
    }
    if (method === 'POST' && path === '/api/v1/conversations') {
      const doc_id = `${GROUP}.00${server.nextChatNo++}-CH`
      const created = { doc_id, title: body.title, type_code: 'CH', updated_at: '2026-10-06T09:00:00Z' }
      server.chats.unshift(created)
      server.turns[doc_id] = []
      return { ok: true, ...created, project_id: body.project_id, group_id: body.group_id, status: 'approved', created_at: created.updated_at }
    }
    throw httpError(404, `no route ${method} ${path}`)
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
  vi.useRealTimers()
})

async function mountPanel() {
  wrapper = mount(ConversationPanel, {
    props: { projectId: 'flowgate', groupId: GROUP },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return wrapper
}

const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)
const findAll = (sel: string) => wrapper!.findAll(`[data-test="${sel}"]`)
const calls = (method: Call['method'], match: RegExp) => transport.calls.filter((c) => c.method === method && match.test(c.path))

/** One AI-run polling tick of the session (it sleeps RUN_POLL_MS between polls). */
async function tick() {
  await vi.advanceTimersByTimeAsync(RUN_POLL_MS)
  await flushPromises()
}

describe('Conversation production path (flowgate.v02.0032 T#2)', () => {
  it('pending command → approve → same run carries on → run change and its file diff', async () => {
    const server = createServer()
    await mountPanel()
    expect(calls('GET', /^\/api\/v1\/chat-activity\//)).toHaveLength(1)

    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await find('conv-invoke-ai').trigger('click')
    await flushPromises()
    expect(calls('POST', /^\/api\/v1\/ai-invoke\/start$/)).toHaveLength(1)

    // The model asks to run a command; the server holds it for approval.
    server.commands.push({
      request_id: 'req-1',
      ai_run_id: 'run-1',
      doc_id: CHAT,
      command: 'npm run build',
      program: 'npm',
      args: ['run', 'build'],
      cwd: 'client_gui',
      status: 'pending_approval',
      rejected_by: null,
      error_code: null,
      anchor_seq: 1,
      anchor_position: 'after',
      anchor_state: 'run_start',
      created_at: '2026-10-06T08:02:00Z',
    })
    await tick()
    const card = find('tl-command')
    expect(card.exists()).toBe(true)
    expect(card.attributes('data-state')).toBe('pending_approval')

    await find('tl-command-approve').trigger('click')
    await flushPromises()
    const [decisionCall] = calls('POST', /\/decision$/)
    expect(decisionCall.path).toBe('/api/v1/chat-commands/req-1/decision')
    expect(decisionCall.body).toEqual({ decision: 'approve' })
    expect(find('tl-command').attributes('data-state')).toBe('approved')

    // The same live run runs the command and keeps going; no new run is started.
    server.commands[0].status = 'running'
    await tick()
    expect(find('tl-command').attributes('data-state')).toBe('running')
    expect(calls('POST', /^\/api\/v1\/ai-invoke\/start$/)).toHaveLength(1)

    // The run answers and ends; its change lands after the reply.
    Object.assign(server.commands[0], { status: 'succeeded', exit_code: 0, anchor_seq: 2, anchor_position: 'before', anchor_state: 'reply' })
    server.turns[CHAT].push({ seq: 2, speaker: 'ai', body: '빌드가 통과했습니다', created_at: '2026-10-06T08:05:00Z' })
    server.changes.push({
      run_id: 'run-1',
      doc_id: CHAT,
      files_changed: 1,
      insertions: 1,
      deletions: 1,
      files: [{ path: 'client_gui/build.txt', status: 'M', insertions: 1, deletions: 1 }],
      anchor_seq: 2,
      anchor_position: 'after',
      anchor_state: 'reply',
      created_at: '2026-10-06T08:05:01Z',
    })
    server.run!.status = 'finished'
    await tick()
    await tick()
    const order = find('conv-log')
      .findAll('[data-test="tl-message"], [data-test="tl-command"], [data-test="tl-change"]')
      .map((n) => n.attributes('data-test'))
    expect(order).toEqual(['tl-message', 'tl-command', 'tl-message', 'tl-change'])
    expect(find('tl-command').attributes('data-state')).toBe('succeeded')
    expect(find('conv-send').classes()).not.toContain('is-stop')

    await find('tl-change-file').trigger('click')
    await flushPromises()
    expect(calls('GET', /\/runs\/run-1\/diff$/).at(-1)?.params).toEqual({ path: 'client_gui/build.txt' })
    expect(find('rc-diff-lines').findAll('.rc-diff-line').map((l) => l.attributes('data-kind'))).toEqual(['context', 'del', 'add'])
  })

  it('a reopened panel reads the activity again and shows the server state', async () => {
    const server = createServer()
    server.commands.push({
      request_id: 'req-7',
      ai_run_id: 'run-1',
      doc_id: CHAT,
      command: 'git status',
      status: 'pending_approval',
      anchor_seq: 1,
      anchor_position: 'after',
      anchor_state: 'run_start',
    })
    await mountPanel()
    expect(find('tl-command').attributes('data-state')).toBe('pending_approval')
    wrapper!.unmount()
    server.commands[0].status = 'cancelled'
    server.commands[0].error_code = 'chat_command_approval_wait_expired'
    await mountPanel()
    expect(find('tl-command').attributes('data-state')).toBe('cancelled')
    expect(find('tl-command-reason').text()).toContain('승인 대기 시간이 지나')
    expect(find('tl-command-approve').exists()).toBe(false)
  })

  it('[새 채팅] during an AI run makes a standalone CH through POST /conversations, never next-empty', async () => {
    createServer()
    await mountPanel()
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await find('conv-invoke-ai').trigger('click')
    await flushPromises()
    expect(find('conv-send').classes()).toContain('is-stop')

    await find('conv-new-btn').trigger('click')
    await flushPromises()
    const [create] = calls('POST', /^\/api\/v1\/conversations$/)
    expect(create.body).toEqual({ project_id: 'flowgate', group_id: GROUP, title: '새 대화' })
    expect(transport.calls.some((c) => /next-empty/.test(c.path))).toBe(false)
    // The list is read again and the new chat opens with its own (empty) activity.
    expect(calls('GET', /^\/api\/v1\/documents$/).length).toBeGreaterThanOrEqual(2)
    expect((find('conv-title-input').element as HTMLInputElement).value).toBe('새 대화')
    expect(calls('GET', /^\/api\/v1\/chat-activity\//).at(-1)?.path).toBe(`/api/v1/chat-activity/${GROUP}.0012-CH`)
  })
})
