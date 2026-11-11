import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02test.0023 — the chat commit point: a chat names a commit message, its run end commits the flow's
// worktree with it, and a call without one is refused while the worktree holds uncommitted changes
// (409 `chat_commit_message_required`).
const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
const patchRequest = vi.hoisted(() => vi.fn())
const extractApiErrorMessage = vi.hoisted(() => vi.fn((_error: unknown, fallback: string) => fallback))

vi.mock('@shared/api', () => ({ getRequest, postRequest, patchRequest, extractApiErrorMessage }))

import { ConversationRequestError, fetchRun, startChatRun, type ChatRunState } from '@/features/conversation/api'
import { createConversationSession } from '@/features/conversation/conversationSession'
import { commitLine, runStatusLines } from '@/features/conversation/runAssessment'
import { createFakeSource, turn } from './fakeConversationSource'

const CHAT = 'p.m.0001.0002-CH'

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('api', () => {
  it('sends commit_message only when there is one', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, run_id: 'r1' } })
    await startChatRun(CHAT, 'a', 'run-1b2c3d4e', 'fix(chat): commit')
    expect(postRequest.mock.calls[0][1]).toMatchObject({ commit_message: 'fix(chat): commit', client_request_id: 'run-1b2c3d4e' })
    await startChatRun(CHAT, 'a', 'run-1b2c3d4f')
    expect(postRequest.mock.calls[1][1]).not.toHaveProperty('commit_message')
  })

  it('keeps the refusal body on the error', async () => {
    postRequest.mockRejectedValue(Object.assign(new Error('x'), { response: { status: 409, data: {
      code: 'chat_commit_message_required', message: '2 uncommitted change(s)', file_count: 2, files: ['a.py', 'b.py'] } } }))
    const error = await startChatRun(CHAT, 'a').catch((e) => e)
    expect(error).toBeInstanceOf(ConversationRequestError)
    expect(error).toMatchObject({ status: 409, code: 'chat_commit_message_required' })
    expect(error.data).toMatchObject({ file_count: 2, files: ['a.py', 'b.py'] })
  })

  it('maps the run end commit', async () => {
    getRequest.mockResolvedValue({ data: { run_id: 'r1', status: 'finished', end_reason: 'exited', exit_code: 0,
      chat_commit: { committed: true, commit: 'abc1234', subject: 'fix: x', skipped_reason: null, file_count: 2, files: ['a.py', 'b.py'] } } })
    expect((await fetchRun('r1')).chatCommit).toEqual({ committed: true, commit: 'abc1234', subject: 'fix: x',
      skippedReason: null, fileCount: 2, files: ['a.py', 'b.py'] })
    getRequest.mockResolvedValue({ data: { run_id: 'r2', status: 'finished' } })
    expect(await fetchRun('r2')).not.toHaveProperty('chatCommit')
  })
})

describe('run status line', () => {
  const base: ChatRunState = { runId: 'r1', status: 'finished', endReason: 'exited', outcome: 'complete', exitCode: 0,
    succeeded: true, docRef: CHAT, actionScope: 'chat', groupId: 'p.m.0001', finishedAt: null }
  const commit = (over: Partial<NonNullable<ChatRunState['chatCommit']>>) => ({ ...base, chatCommit: {
    committed: false, commit: null, subject: null, skippedReason: null, fileCount: 0, files: [], ...over } })

  it('is absent without a commit result', () => {
    expect(commitLine(base)).toBeNull()
    expect(runStatusLines(base, 'present', 'complete')).toEqual([])
  })

  it('shows a commit, a missing message and a skip', () => {
    expect(commitLine(commit({ committed: true, commit: 'abc1234', subject: 'fix: x', fileCount: 2 })))
      .toEqual({ slot: 'commit', tone: 'info', code: 'chat_committed', text: 'abc1234 · fix: x · 2 files' })
    expect(commitLine(commit({ skippedReason: 'message_missing', fileCount: 4, files: ['a', 'b', 'c', 'd'] })))
      .toMatchObject({ tone: 'warning', code: 'chat_commit_missing', text: 'a, b, c (+1)' })
    expect(commitLine(commit({ skippedReason: 'git_busy' })))
      .toMatchObject({ tone: 'warning', code: 'chat_commit_skipped', text: 'git_busy' })
  })

  it('is shown for a completed and for a cancelled run', () => {
    const done = commit({ committed: true, commit: 'abc1234', fileCount: 1 })
    expect(runStatusLines(done, 'present', 'complete').map((l) => l.code)).toEqual(['chat_committed'])
    expect(runStatusLines({ ...done, endReason: 'cancelled' }, 'present', 'complete').map((l) => l.code))
      .toEqual(['run_cancelled', 'chat_committed'])
  })
})

describe('session', () => {
  async function setup() {
    const fake = createFakeSource({
      chats: [{ id: CHAT, title: 'Chat', updatedAt: '2026-11-12T00:00:00Z' }],
      turns: { [CHAT]: [turn(1, 'user', 'fix it', '2026-11-12T00:00:00Z')] },
    })
    const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
    await session.setScope({ projectId: 'p', groupId: 'p.m.0001' })
    await flushPromises()
    session.selectProvider(session.state.providers[0]?.id ?? '')
    return { ...fake, session }
  }

  it('asks for a message on the refusal, then calls again with it and clears it', async () => {
    const { session, source } = await setup()
    source.startChatRun.mockRejectedValueOnce(new ConversationRequestError('2 uncommitted change(s): a.py, b.py', 409,
      'chat_commit_message_required', null, { file_count: 2, files: ['a.py', 'b.py'] }))
    await session.invokeAi()
    expect(session.state.commitRequired).toEqual({ fileCount: 2, files: ['a.py', 'b.py'] })
    // flowgate.v02test.0024 B: the refusal is a line of the chat's history (no longer the notice above the composer).
    expect(session.state.notice).toBeNull()
    expect(session.state.activities.filter((a) => a.kind === 'status')).toMatchObject([
      { code: 'commit_required', tone: 'warning', text: '2 uncommitted change(s): a.py, b.py', chatError: true }])
    expect(session.state.runId).toBeNull()

    session.setCommitMessage('  fix(chat): leftover edits ')
    await session.invokeAi()
    expect(source.startChatRun).toHaveBeenLastCalledWith(CHAT, expect.any(String), expect.any(String), 'fix(chat): leftover edits')
    expect(session.state.commitMessage).toBe('')
    expect(session.state.commitRequired).toBeNull()
  })

  it('a call without a message keeps the three-argument call', async () => {
    const { session, source } = await setup()
    await session.invokeAi()
    expect(source.startChatRun.mock.calls.at(-1)).toHaveLength(3)
  })

  it('another Flow forgets the message and the refusal', async () => {
    const { session } = await setup()
    session.setCommitMessage('fix: x')
    await session.setScope({ projectId: 'q', groupId: 'q.m.0001' })
    expect(session.state.commitMessage).toBe('')
    expect(session.state.commitRequired).toBeNull()
  })
})
