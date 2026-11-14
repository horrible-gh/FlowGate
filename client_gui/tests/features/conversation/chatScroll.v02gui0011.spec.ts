import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { createConversationSession } from '@/features/conversation/conversationSession'
import type { AiRunState } from '@/features/conversation/types'
import { createFakeSource, turn } from './fakeConversationSource'

// flowgate.v02gui.0011 A — opening a long chat showed every message: restoreRuns' final read starts at seq 0 to look
// for the reply of a run whose body is not on screen, and merged everything it read into the list. Now only turns from
// the oldest one on screen on are merged; the older ones are evidence only, and scrolling up still loads them.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const HEAD = 120

function finished(runId: string): AiRunState {
  return { runId, status: 'finished', endReason: 'exited', outcome: 'complete', exitCode: 0, docRef: A, actionScope: 'chat', groupId: null } as AiRunState
}

async function setup(all: ReturnType<typeof turn>[], runs: string[]) {
  const runDetails = Object.fromEntries(runs.map((id) => [id, finished(id)]))
  const fake = createFakeSource({ chats: [{ id: A, title: 'long', updatedAt: null }], turns: { [A]: [] }, runDetails })
  const source = fake.source
  source.fetchTurns.mockImplementation(async (_chatId: string, query: { afterSeq?: number; beforeSeq?: number; limit?: number }) => {
    const list = query.beforeSeq != null
      ? all.filter((t) => t.seq < query.beforeSeq!).slice(-(query.limit ?? 30))
      : query.afterSeq != null
        ? all.filter((t) => t.seq > query.afterSeq!)
        : all.slice(-(query.limit ?? 50))
    const oldest = list[0]?.seq ?? 1
    return { turns: list.map((t) => ({ ...t })), participants: [], myReadSeq: null, headSeq: HEAD, nextAfterSeq: null, prevBeforeSeq: oldest > 1 ? oldest : null }
  })
  const session = createConversationSession({ source, sleep: async () => undefined })
  await session.setScope(SCOPE)
  await flushPromises()
  const fromZero = () => source.fetchTurns.mock.calls.some((c: unknown[]) => (c[1] as { afterSeq?: number }).afterSeq === 0)
  const statusCodes = (runId: string) => session.state.activities
    .filter((a) => a.kind === 'status' && (a as { runId?: string }).runId === runId).map((a) => (a as { code: string }).code)
  return { session, source, fromZero, statusCodes }
}

describe('v02gui.0011 A: a long chat opens on its recent messages only', () => {
  it('a run whose reply is above the first page reads from 0 but leaves the older turns off screen', async () => {
    const all = Array.from({ length: HEAD }, (_, i) =>
      turn(i + 1, i % 2 ? 'ai' : 'user', `m${i + 1}`, null, i + 1 === 6 ? { sourceRunId: 'run-old' } : {}))
    const { session, fromZero, statusCodes } = await setup(all, ['run-old'])
    expect(fromZero()).toBe(true) // the reply of run-old is not on screen: the final read starts at 0
    expect(session.state.turns[0].seq).toBe(71)
    expect(session.state.turns).toHaveLength(50)
    expect(session.state.hasMoreBefore).toBe(true)
    // The reply seen above the screen still counts as stored: no "no reply" line for it.
    expect(statusCodes('run-old')).not.toContain('run_no_reply')
    expect(statusCodes('run-old')).not.toContain('run_query_unconfirmed')
  })

  it('a run with no reply anywhere is still reported, and scrolling up loads the older pages', async () => {
    const all = Array.from({ length: HEAD }, (_, i) => turn(i + 1, 'user', `m${i + 1}`, null))
    const { session, statusCodes } = await setup(all, ['run-lost'])
    expect(session.state.turns[0].seq).toBe(71)
    expect(statusCodes('run-lost')).toContain('run_no_reply')
    await session.loadOlder()
    expect(session.state.turns[0].seq).toBe(41)
    expect(session.state.hasMoreBefore).toBe(true)
  })

  it('an unattributed AI reply above the screen keeps the attribution warning instead of "no reply"', async () => {
    const all = Array.from({ length: HEAD }, (_, i) => turn(i + 1, i === 9 ? 'ai' : 'user', `m${i + 1}`, null))
    const { session, statusCodes } = await setup(all, ['run-x'])
    expect(session.state.turns[0].seq).toBe(71)
    expect(statusCodes('run-x')).toContain('run_attribution_unconfirmed')
  })

  it('a chat that is fully on screen behaves as before', async () => {
    const all = Array.from({ length: 20 }, (_, i) =>
      turn(i + 1, i % 2 ? 'ai' : 'user', `m${i + 1}`, null, i + 1 === 2 ? { sourceRunId: 'run-old' } : {}))
    const { session, statusCodes } = await setup(all, ['run-old'])
    expect(session.state.turns[0].seq).toBe(1)
    expect(session.state.hasMoreBefore).toBe(false)
    expect(statusCodes('run-old')).not.toContain('run_no_reply')
  })
})
