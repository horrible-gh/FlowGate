import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { createConversationSession, type ConversationSession } from '@/features/conversation/conversationSession'
import { createFakeSource, turn } from './fakeConversationSource'

// These black-box tests also run against the unmodified product to record the defect.
// They use only the pre-existing session/source surface; no copy of the judgment logic.
const A = 'p.m.0001.0002-CH'
const scope = { projectId: 'p', groupId: 'p.m.0001' }
const live: ConversationSession[] = []
afterEach(() => { for (const s of live.splice(0)) s.dispose() })

describe('0147 before/after six-condition reproduction', () => {
  it.each([1, 2, 3, 4, 5, 6])('condition %s', async (condition) => {
    const reply = turn(2, 'ai', 'isolated reply', null, { sourceRunId: 'r1' })
    const fake = createFakeSource({ chats: [{ id: A, title: 'isolated chat', updatedAt: null }],
      turns: { [A]: [turn(1, 'user', 'isolated input'), ...(condition === 2 ? [reply] : [])] },
      activeRun: condition === 2 ? 'r1' : null })
    const waiting: Array<() => void> = []
    const session = createConversationSession({ source: fake.source, sleep: () => new Promise<void>((resolve) => waiting.push(resolve)) })
    live.push(session)
    fake.source.startChatRun.mockResolvedValue('r1')
    fake.source.fetchRun.mockResolvedValue({ runId: 'r1', status: 'finished', docRef: A, actionScope: 'chat', groupId: scope.groupId,
      endReason: condition === 4 ? 'timeout' : condition === 6 ? 'cancelled' : 'exited',
      outcome: condition === 5 ? null : condition === 4 || condition === 6 ? 'none' : 'complete',
      exitCode: condition === 5 || condition === 4 || condition === 6 ? null : 0 })
    await session.setScope(scope); await flushPromises()
    fake.source.fetchTurns.mockClear()
    if (condition === 3) fake.source.fetchTurns.mockRejectedValue(new Error('isolated GET failure'))
    if (condition !== 2) await session.invokeAi()
    if (condition === 1 || condition === 4 || condition === 6) fake.data.turns[A].push(reply)
    for (let attempt = 0; attempt < 3; attempt++) { waiting.shift()?.(); await flushPromises() }
    const codes = session.state.activities.filter((a) => a.kind === 'status').map((a) => a.code)
    console.log('0147_run_evidence', JSON.stringify({ condition, chat: A, run: 'r1', turnReads: fake.source.fetchTurns.mock.calls.length,
      starts: fake.source.startChatRun.mock.calls.length, posts: fake.source.postTurn.mock.calls.length,
      bodySeqs: session.state.turns.filter((t) => t.speaker === 'ai').map((t) => t.seq), codes, running: session.running.value }))
    const expected = condition === 3 ? ['run_query_unconfirmed'] : condition === 4 ? ['run_failed'] :
      condition === 5 ? ['run_result_unconfirmed'] : condition === 6 ? ['run_cancelled'] : []
    expect(codes).toEqual(expected)
    expect(fake.source.fetchTurns).toHaveBeenCalledTimes(condition === 3 ? 3 : 1)
    expect(fake.source.startChatRun).toHaveBeenCalledTimes(condition === 2 ? 0 : 1)
    expect(fake.source.postTurn).not.toHaveBeenCalled()
    expect(session.running.value).toBe(false)
  })
})
