import { vi } from 'vitest'
import { ConversationRequestError, DEFAULT_CHAT_SETTINGS } from '@/features/conversation/api'
import { chatErrorActivity, type ChatErrorRecord } from '@/features/conversation/chatErrors'
import type { ConversationSource } from '@/features/conversation/conversationSource'
import type {
  AiRunState,
  ChatSettings,
  ChatSummary,
  CommandRequestActivity,
  ConversationActivity,
  ConversationParticipant,
  ConversationScope,
  ConversationTurn,
  RunFileDiff,
} from '@/features/conversation/types'

// flowgate.v02.0031 — in-memory ConversationSource for the session / panel specs. Every
// method is a vi.fn so a spec can assert calls or override one answer. flowgate.v02.0032: it
// behaves like the server for activities — a decision changes what the next read returns.
export interface FakeData {
  chats: ChatSummary[]
  turns: Record<string, ConversationTurn[]>
  activities: Record<string, ConversationActivity[]>
  participants: ConversationParticipant[]
  settings: ChatSettings
  /** Run id → statuses returned by successive fetchRun calls (last one repeats). */
  runs: Record<string, Array<'running' | 'finished'>>
  /** A turn that lands in a chat once fetchRun for this run reports finished. */
  replies: Record<string, { chatId: string; turn: ConversationTurn }>
  activeRun: string | null
  runDetails: Record<string, AiRunState>
  /** `${runId}\n${path}` → stored run change file diff. */
  diffs: Record<string, RunFileDiff>
  /** flowgate.v02.0152: client_request_id → the run the "server" admitted for that press (a resend gets it back). */
  admitted: Record<string, string>
  /** flowgate.v02test.0024 B: chat id → the chat errors the "server" recorded (read back with the activities). */
  chatErrors: Record<string, ChatErrorRecord[]>
}

export function turn(seq: number, speaker: 'user' | 'ai', body: string, createdAt: string | null = null, extra: Partial<ConversationTurn> = {}): ConversationTurn {
  return { seq, speaker, body, createdAt, ...extra }
}

export function createFakeSource(init: Partial<FakeData> = {}): { source: ConversationSource & Record<string, any>; data: FakeData } {
  const data: FakeData = {
    chats: init.chats ?? [],
    turns: init.turns ?? {},
    activities: init.activities ?? {},
    participants: init.participants ?? [],
    settings: init.settings ?? { ...DEFAULT_CHAT_SETTINGS },
    runs: init.runs ?? {},
    replies: init.replies ?? {},
    activeRun: init.activeRun ?? null,
    runDetails: init.runDetails ?? {},
    diffs: init.diffs ?? {},
    admitted: init.admitted ?? {},
    chatErrors: init.chatErrors ?? {},
  }
  let nextSeq = 100
  const owedTitles = new Map<string, string>(data.chats.filter((c) => c.autoTitle?.state === 'owed').map((c) => [c.id, c.autoTitle!.provisionalTitle]))
  let runCounter = 0
  const runCalls: Record<string, number> = {}
  const runChats: Record<string, string> = {}

  const source = {
    listChats: vi.fn(async () => data.chats.map((c) => ({ ...c }))),
    renameChat: vi.fn(async (chatId: string, title: string) => {
      const chat = data.chats.find((c) => c.id === chatId)!
      owedTitles.delete(chatId)
      chat.autoTitle = { state: 'manual', provisionalTitle: chat.autoTitle?.provisionalTitle ?? chat.title }
      chat.title = title
      return { ...chat }
    }),
    // flowgate.v02.0047: like the server, the debt is kept here — paid once, ended by a rename;
    // the title is derived from the first user turn.
    autoTitleChat: vi.fn(async (chatId: string, _providerId: string) => {
      const chat = data.chats.find((c) => c.id === chatId)!
      const provisional = owedTitles.get(chatId)
      if (provisional === undefined) throw new ConversationRequestError('not owed', 409, 'conversation_auto_title_not_owed')
      if (chat.title !== provisional) throw new ConversationRequestError('changed', 409, 'conversation_title_changed')
      const first = (data.turns[chatId] ?? []).find((t) => t.speaker === 'user' && t.body.trim())
      if (!first) throw new ConversationRequestError('no user turn', 409, 'conversation_auto_title_no_message')
      owedTitles.delete(chatId)
      chat.autoTitle = { state: 'done', provisionalTitle: provisional }
      chat.title = `AI: ${first.body}`
      return { ...chat }
    }),
    createChat: vi.fn(async (_scope: unknown, title: string, options?: { autoTitle?: boolean }) => {
      const chat: ChatSummary = { id: `p.m.0001.00${data.chats.length + 10}-CH`, title, updatedAt: '2026-10-06T09:00:00Z',
        ...(options?.autoTitle ? { autoTitle: { state: 'owed', provisionalTitle: title } as const } : {}) }
      if (options?.autoTitle) owedTitles.set(chat.id, title)
      data.chats.unshift(chat)
      data.turns[chat.id] = []
      return { ...chat }
    }),
    fetchTurns: vi.fn(async (chatId: string, query: { afterSeq?: number; beforeSeq?: number }) => {
      const all = data.turns[chatId] ?? []
      let list = all
      if (query.afterSeq != null) list = all.filter((t) => t.seq > query.afterSeq!)
      if (query.beforeSeq != null) list = all.filter((t) => t.seq < query.beforeSeq!)
      const head = all.reduce((m, t) => Math.max(m, t.seq), 0)
      return { turns: list.map((t) => ({ ...t })), participants: data.participants, myReadSeq: null, headSeq: head, nextAfterSeq: null, prevBeforeSeq: null }
    }),
    postTurn: vi.fn(async (chatId: string, input: { body: string }) => {
      nextSeq += 1
      const saved = turn(nextSeq, 'user', input.body, '2026-10-06T10:00:00Z')
      ;(data.turns[chatId] ??= []).push(saved)
      return { turn: { ...saved }, headSeq: nextSeq }
    }),
    markRead: vi.fn(async (_chatId: string, seq: number) => seq),
    supportsActivities: true,
    listActivities: vi.fn(async (chatId: string) => [
      ...(data.activities[chatId] ?? []).map((a) => ({ ...a })),
      ...(data.chatErrors[chatId] ?? []).map((record) => chatErrorActivity(chatId, record)),
    ]),
    decideCommand: vi.fn(async (chatId: string, commandId: string, decision: 'approve' | 'reject') => {
      const list = data.activities[chatId] ?? []
      const at = list.findIndex((a) => a.id === commandId)
      const found = list[at] as CommandRequestActivity
      const decided: CommandRequestActivity =
        decision === 'approve' ? { ...found, state: 'approved' } : { ...found, state: 'rejected', rejectedBy: 'user' }
      list.splice(at, 1, decided)
      return { ...decided }
    }),
    fetchRunFileDiff: vi.fn(async (_chatId: string, runId: string, path: string) => {
      const diff = data.diffs[`${runId}\n${path}`]
      if (!diff) throw new ConversationRequestError('not found', 404, 'chat_run_change_path_not_found')
      return { ...diff }
    }),
    listProviders: vi.fn(async () => ({ providers: [{ id: 'opus', name: 'Opus 5' }, { id: 'sonnet', name: 'Sonnet 5' }], defaultProviderId: 'opus' })),
    // flowgate.v02.0152: like the server, a press it has already admitted gets its run back, never a second one.
    startChatRun: vi.fn(async (chatId: string, _providerId?: string | null, clientRequestId?: string) => {
      if (clientRequestId && data.admitted[clientRequestId]) return data.admitted[clientRequestId]
      runCounter += 1
      const id = `run-${runCounter}`
      runChats[id] = chatId
      data.runs[id] ??= ['running', 'finished']
      if (clientRequestId) data.admitted[clientRequestId] = id
      return id
    }),
    fetchRun: vi.fn(async (runId: string) => {
      const seq = data.runs[runId] ?? ['finished']
      const i = Math.min(runCalls[runId] ?? 0, seq.length - 1)
      runCalls[runId] = (runCalls[runId] ?? 0) + 1
      const status = seq[i]
      const reply = data.replies[runId]
      if (status === 'finished' && reply) {
        const list = (data.turns[reply.chatId] ??= [])
        if (!list.some((t) => t.seq === reply.turn.seq)) list.push({ ...reply.turn, sourceRunId: reply.turn.sourceRunId ?? runId })
      }
      if (data.runDetails[runId]) return { ...data.runDetails[runId] }
      return { runId, status, endReason: status === 'finished' ? 'exited' : null,
        outcome: status === 'finished' ? 'complete' : null, exitCode: status === 'finished' ? 0 : null,
        docRef: reply?.chatId ?? runChats[runId] ?? data.chats.find((chat) =>
          (data.activities[chat.id] ?? []).some((activity) => activity.runId === runId))?.id ?? data.chats[0]?.id ?? null,
        actionScope: 'chat', groupId: null }

    }),
    listRuns: vi.fn(async (_scope: unknown, limit: number) => {
      const all = Object.values(data.runDetails).map((run) => ({ runId: run.runId, docRef: run.docRef ?? null }))
      return { items: all.slice(0, limit), total: all.length, hasMore: all.length > limit }
    }),
    cancelRun: vi.fn(async () => undefined),
    fetchActiveChatRun: vi.fn(async () => data.activeRun),
    fetchChatSettings: vi.fn(async (scope?: ConversationScope) => ({ ...data.settings, sourceAccessScope: scope ?? null })),
    saveChatSettings: vi.fn(async (patch: Record<string, unknown>, scope?: ConversationScope) => {
      if (patch.send_action) data.settings.sendAction = patch.send_action as ChatSettings['sendAction']
      if (patch.context_mode) data.settings.contextMode = patch.context_mode as ChatSettings['contextMode']
      if (patch.context_turns) data.settings.contextTurns = patch.context_turns as number
      if (patch.source_access_mode) data.settings.sourceAccessMode = patch.source_access_mode as ChatSettings['sourceAccessMode']
      return { ...data.settings, sourceAccessScope: scope ?? null }
    }),
    issueChatMention: vi.fn(async (chatId: string) => `@mention ${chatId}`),
    // flowgate.v02test.0024 B: like the server, one record per id (a repeat is answered, not stored twice).
    recordChatError: vi.fn(async (chatId: string, record: ChatErrorRecord) => {
      const list = (data.chatErrors[chatId] ??= [])
      if (!list.some((r) => r.id === record.id)) list.push({ ...record })
    }),
  }
  return { source, data }
}

export function httpError(status: number, message = 'failed', code: string | null = null): ConversationRequestError {
  return new ConversationRequestError(message, status, code)
}
