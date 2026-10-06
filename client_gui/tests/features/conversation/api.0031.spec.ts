import { beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02.0031 — the Conversation adapter calls only the existing conversation / AI-run /
// chat-settings / document contracts (R0001 §9) and normalizes their snake_case answers.
const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
const patchRequest = vi.hoisted(() => vi.fn())
const extractApiErrorMessage = vi.hoisted(() => vi.fn((_error: unknown, fallback: string) => fallback))

vi.mock('@shared/api', () => ({ getRequest, postRequest, patchRequest, extractApiErrorMessage }))

import {
  ConversationRequestError,
  cancelRun,
  createChat,
  fetchActiveChatRun,
  fetchChatSettings,
  fetchChats,
  fetchProviders,
  fetchRun,
  fetchTurns,
  issueChatMention,
  markRead,
  postTurn,
  renameChat,
  saveChatSettings,
  splitGroupId,
  startChatRun,
} from '@/features/conversation/api'
import { createHttpConversationSource } from '@/features/conversation/conversationSource'

const SCOPE = { projectId: 'flowgate', groupId: 'flowgate.v02.0031' }

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
  patchRequest.mockReset()
  extractApiErrorMessage.mockClear()
})

describe('chat list / title / new chat', () => {
  it('lists the CH documents of the Flow, newest first', async () => {
    getRequest.mockResolvedValue({
      data: [
        { doc_id: 'flowgate.v02.0031.0003-CH', title: 'Old', updated_at: '2026-10-04T00:00:00Z' },
        { doc_id: 'flowgate.v02.0031.0005-CH', title: '', updated_at: '2026-10-06T00:00:00Z' },
      ],
    })
    const chats = await fetchChats(SCOPE)
    expect(getRequest).toHaveBeenCalledWith('/api/v1/documents', {
      project_id: 'flowgate',
      group_id: 'flowgate.v02.0031',
      type_code: 'CH',
      limit: 200,
    })
    expect(chats).toEqual([
      { id: 'flowgate.v02.0031.0005-CH', title: 'flowgate.v02.0031.0005-CH', updatedAt: '2026-10-06T00:00:00Z' },
      { id: 'flowgate.v02.0031.0003-CH', title: 'Old', updatedAt: '2026-10-04T00:00:00Z' },
    ])
  })

  it('renames through PATCH /documents/{id}', async () => {
    patchRequest.mockResolvedValue({ data: { doc_id: 'p.m.0001.0002-CH', title: 'New name', updated_at: 'x' } })
    const saved = await renameChat('p.m.0001.0002-CH', 'New name')
    expect(patchRequest).toHaveBeenCalledWith('/api/v1/documents/p.m.0001.0002-CH', { title: 'New name' })
    expect(saved.title).toBe('New name')
  })

  // flowgate.v02.0032 (D0006 §3.10, §5.4): a standalone CH through POST /conversations — no
  // member-document lookup, no prev_doc_id, no module, no next-empty.
  it('creates a standalone chat through POST /conversations with the scope and the title only', async () => {
    postRequest.mockResolvedValue({
      data: {
        ok: true,
        doc_id: 'flowgate.v02.0031.0007-CH',
        project_id: 'flowgate',
        group_id: 'flowgate.v02.0031',
        type_code: 'CH',
        title: '새 대화',
        status: 'approved',
        created_at: '2026-10-06T02:00:00Z',
        updated_at: '2026-10-06T02:00:01Z',
      },
    })
    const created = await createChat(SCOPE, '새 대화')
    expect(getRequest).not.toHaveBeenCalled()
    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(postRequest).toHaveBeenCalledWith('/api/v1/conversations', { project_id: 'flowgate', group_id: 'flowgate.v02.0031', title: '새 대화' })
    expect(created).toEqual({ id: 'flowgate.v02.0031.0007-CH', title: '새 대화', updatedAt: '2026-10-06T02:00:01Z' })
  })

  it('surfaces a refusal with its HTTP status and the envelope code', async () => {
    const error = Object.assign(new Error('x'), {
      response: { status: 409, data: { ok: false, error: { code: 'conversation_number_reservation_failed', message: 'retry' } } },
    })
    postRequest.mockRejectedValue(error)
    await expect(createChat(SCOPE, 'x')).rejects.toMatchObject({ status: 409, code: 'conversation_number_reservation_failed' })
  })

  it('refuses an answer without a document id', async () => {
    postRequest.mockResolvedValue({ data: { ok: true } })
    await expect(createChat(SCOPE, 'x')).rejects.toBeInstanceOf(ConversationRequestError)
  })
})

describe('turns', () => {
  it('reads a page with the same query names as the v0.1 ConversationView', async () => {
    getRequest.mockResolvedValue({
      data: {
        turns: [{ seq: 4, speaker: 'ai', participant_key: 'ai:opus', display_name: 'Opus', body: 'hi', created_at: 'T', stale_since_seq: 3 }],
        participants: [{ participant_key: 'u:test', kind: 'user', display_name: 'test', last_read_seq: 4 }],
        me: { participant_key: 'u:test', last_read_seq: 4 },
        head_seq: 4,
        next_after_seq: null,
        prev_before_seq: 1,
      },
    })
    const page = await fetchTurns('p.m.0001.0002-CH', { includeHead: true, limit: 50 })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/documents/p.m.0001.0002-CH/conversation/turns', { include_head: 1, limit: 50 })
    expect(page).toEqual({
      turns: [{ seq: 4, speaker: 'ai', participantKey: 'ai:opus', displayName: 'Opus', body: 'hi', basedOnSeq: null, staleSinceSeq: 3, createdAt: 'T' }],
      participants: [{ key: 'u:test', kind: 'user', displayName: 'test', lastReadSeq: 4 }],
      myReadSeq: 4,
      headSeq: 4,
      nextAfterSeq: null,
      prevBeforeSeq: 1,
    })
    await fetchTurns('c', { afterSeq: 7, limit: 50 })
    expect(getRequest).toHaveBeenLastCalledWith('/api/v1/documents/c/conversation/turns', { after_seq: 7, limit: 50 })
    await fetchTurns('c', { beforeSeq: 3, limit: 30 })
    expect(getRequest).toHaveBeenLastCalledWith('/api/v1/documents/c/conversation/turns', { before_seq: 3, limit: 30 })
  })

  it('posts a turn with its idempotency key and reports read positions', async () => {
    postRequest.mockResolvedValueOnce({ data: { turn: { seq: 9, speaker: 'user', body: 'x' }, head_seq: 9 } })
    const res = await postTurn('c', { body: 'x', idempotencyKey: 'sess_1', basedOnSeq: 8 })
    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/c/conversation/turn', { body: 'x', idempotency_key: 'sess_1', based_on_seq: 8 })
    expect(res.turn?.seq).toBe(9)
    expect(res.headSeq).toBe(9)
    postRequest.mockResolvedValueOnce({ data: { me: { participant_key: 'u', last_read_seq: 9 } } })
    expect(await markRead('c', 9)).toBe(9)
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/documents/c/conversation/read', { last_read_seq: 9, reason: 'viewed' })
  })

  it('keeps the HTTP status of a failure', async () => {
    postRequest.mockRejectedValue(Object.assign(new Error('x'), { response: { status: 422, data: { detail: 'bad' } } }))
    const error = await postTurn('c', { body: 'x', idempotencyKey: 'k', basedOnSeq: 0 }).catch((e) => e)
    expect(error).toBeInstanceOf(ConversationRequestError)
    expect(error.status).toBe(422)
  })
})

describe('AI run', () => {
  it('lists providers of the project', async () => {
    getRequest.mockResolvedValue({ data: { providers: [{ id: 'a', name: 'Opus 5' }, { id: 'b' }], default_provider_id: 'b' } })
    expect(await fetchProviders('flowgate')).toEqual({ providers: [{ id: 'a', name: 'Opus 5' }, { id: 'b', name: 'b' }], defaultProviderId: 'b' })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/providers', { project: 'flowgate' })
  })

  it('starts a chat run with action_scope chat, reads, cancels and finds the active one', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, run_id: 'r1' } })
    expect(await startChatRun('flowgate.v02.0031.0003-CH', 'a')).toBe('r1')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/start', {
      project: 'flowgate',
      module: 'v02',
      group: '0031',
      doc_ref: 'flowgate.v02.0031.0003-CH',
      action_scope: 'chat',
      mode: 'single',
      provider_id: 'a',
    })
    getRequest.mockResolvedValueOnce({ data: { status: 'finished', end_reason: 'cancelled' } })
    expect(await fetchRun('r1')).toEqual({ runId: 'r1', status: 'finished', endReason: 'cancelled' })
    postRequest.mockResolvedValueOnce({ data: {} })
    await cancelRun('r1')
    expect(postRequest).toHaveBeenLastCalledWith('/api/v1/ai-invoke/r1/cancel', {})
    getRequest.mockResolvedValueOnce({ data: { active: true, run_id: 'r2', status: 'running', doc_ref: 'flowgate.v02.0031.0003-CH' } })
    expect(await fetchActiveChatRun('flowgate.v02.0031.0003-CH')).toBe('r2')
    expect(getRequest).toHaveBeenLastCalledWith('/api/v1/ai-invoke/active', { group_id: 'flowgate.v02.0031' })
    getRequest.mockResolvedValueOnce({ data: { active: true, run_id: 'r3', status: 'running', doc_ref: 'other' } })
    expect(await fetchActiveChatRun('flowgate.v02.0031.0003-CH')).toBeNull()
  })

  it('carries code and run id of a 409 run_in_progress', async () => {
    postRequest.mockRejectedValue(Object.assign(new Error('x'), { response: { status: 409, data: { code: 'run_in_progress', run_id: 'r9' } } }))
    const error = await startChatRun('p.m.0001.0002-CH', null).catch((e) => e)
    expect(error).toMatchObject({ status: 409, code: 'run_in_progress', runId: 'r9' })
  })
})

describe('chat settings / mention', () => {
  it('normalizes settings and fails closed on unknown source modes', async () => {
    getRequest.mockResolvedValue({
      data: {
        settings: { send_action: 'invoke_ai', context_mode: 'all', context_turns: 15, source_access_mode: 'edit_once' },
        domain: { context_turns_presets: [10, 20], context_turns_min: 1, context_turns_max: 100, source_access_mode: ['read_only', 'weird', 'edit_once'] },
      },
    })
    expect(await fetchChatSettings()).toEqual({
      sendAction: 'invoke_ai',
      contextMode: 'all',
      contextTurns: 15,
      sourceAccessMode: 'edit_once',
      contextTurnsPresets: [10, 20],
      contextTurnsMin: 1,
      contextTurnsMax: 100,
      sourceAccessModes: ['read_only', 'edit_once'],
    })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/me/chat-settings')
  })

  it('patches only the fields given', async () => {
    patchRequest.mockResolvedValue({ data: { settings: { source_access_mode: 'edit' }, domain: {} } })
    const saved = await saveChatSettings({ source_access_mode: 'edit' })
    expect(patchRequest).toHaveBeenCalledWith('/api/v1/me/chat-settings', { source_access_mode: 'edit' })
    expect(saved.sourceAccessMode).toBe('edit')
    expect(saved.sourceAccessModes).toEqual([])
  })

  it('issues the compact chat mention through /token/issue', async () => {
    postRequest.mockResolvedValue({ data: { mention: '@ai read …' } })
    expect(await issueChatMention('flowgate.v02.0031.0003-CH')).toBe('@ai read …')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/token/issue', {
      project: 'flowgate',
      module: 'v02',
      group: '0031',
      doc_ref: 'flowgate.v02.0031.0003-CH',
      action_scope: 'chat',
    })
    postRequest.mockResolvedValue({ data: {} })
    await expect(issueChatMention('flowgate.v02.0031.0003-CH')).rejects.toBeInstanceOf(ConversationRequestError)
  })
})

describe('HTTP source', () => {
  it('is connected to the production contract and has no push channel (D0006 §5.5)', async () => {
    const source = createHttpConversationSource()
    expect(source.supportsActivities).toBe(true)
    expect(source.subscribeActivities).toBeUndefined()
    getRequest.mockResolvedValue({ data: { ok: true, commands: [], changes: [] } })
    expect(await source.listActivities('flowgate.v02.0031.0003-CH')).toEqual([])
    expect(getRequest).toHaveBeenCalledWith('/api/v1/chat-activity/flowgate.v02.0031.0003-CH')
    postRequest.mockResolvedValue({ data: { ok: true, request: { request_id: 'k', status: 'approved' } } })
    expect((await source.decideCommand('c', 'k', 'approve')).state).toBe('approved')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/chat-commands/k/decision', { decision: 'approve' })
  })

  it('splits group ids', () => {
    expect(splitGroupId('flowgate.v02.0031')).toEqual({ project: 'flowgate', module: 'v02', group: '0031' })
    expect(splitGroupId('flowgate.v02')).toBeNull()
  })
})
