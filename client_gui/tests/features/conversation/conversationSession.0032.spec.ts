import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { RUN_POLL_MS, createConversationSession } from '@/features/conversation/conversationSession'
import type { CommandRequestActivity, ConversationActivity, WorkChangeActivity } from '@/features/conversation/types'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02.0032 — the session on the Conversation production contract (D0006 §3.9.2,
// §6.2, §6.3): activity re-reads on the AI-run polling ticks, decision and new-chat recovery.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0004-CH'

function pending(id: string, extra: Partial<CommandRequestActivity> = {}): CommandRequestActivity {
  return {
    kind: 'command',
    id,
    runId: 'run-1',
    createdAt: null,
    command: `git ${id}`,
    state: 'pending_approval',
    anchor: { seq: 2, position: 'after', state: 'run_start' },
    ...extra,
  }
}

const runChange: WorkChangeActivity = {
  kind: 'change',
  id: 'run-1',
  runId: 'run-1',
  createdAt: null,
  files: [{ path: 'src/a.ts', status: 'M', insertions: 2, deletions: 1 }],
  filesChanged: 1,
  insertions: 2,
  deletions: 1,
  anchor: { seq: 3, position: 'after', state: 'reply' },
}

/** A sleep the spec releases one polling tick at a time. */
function manualSleep() {
  const waiting: Array<() => void> = []
  const sleep = vi.fn((_ms: number) => new Promise<void>((resolve) => waiting.push(resolve)))
  return {
    sleep,
    async tick() {
      await flushPromises()
      waiting.shift()?.()
      await flushPromises()
    },
  }
}

function setup(init: Parameters<typeof createFakeSource>[0] = {}, sleep: (ms: number) => Promise<void> = async () => undefined) {
  const fake = createFakeSource({
    chats: [
      { id: A, title: 'Auth bug', updatedAt: '2026-10-06T08:00:00Z' },
      { id: B, title: 'Git conflict', updatedAt: '2026-10-05T08:00:00Z' },
    ],
    turns: {
      [A]: [turn(1, 'user', 'look', '2026-10-06T08:02:00Z'), turn(2, 'user', 'run the build', '2026-10-06T08:03:00Z')],
      [B]: [turn(1, 'user', 'other chat', '2026-10-05T08:00:00Z')],
    },
    ...init,
  })
  const session = createConversationSession({ source: fake.source, sleep })
  return { ...fake, session }
}

const keys = (list: readonly ConversationActivity[]) => list.map((a) => `${a.kind}:${a.id}`)
const commandOf = (list: readonly ConversationActivity[], id: string) => list.find((a): a is CommandRequestActivity => a.kind === 'command' && a.id === id)

describe('activity re-read during an AI run (D0006 §3.9.2)', () => {
  it('shows a pending command within one polling tick and the run change right after the run ends', async () => {
    const clock = manualSleep()
    const { session, source, data } = setup({ runs: { 'run-1': ['running', 'running', 'finished'] } }, clock.sleep)
    source.startChatRun.mockResolvedValueOnce('run-1')
    await session.setScope(SCOPE)
    await flushPromises()
    expect(source.listActivities).toHaveBeenCalledTimes(1) // chat opened

    await session.invokeAi()
    expect(clock.sleep).toHaveBeenCalledWith(RUN_POLL_MS)
    // The AI asks for a command while the run is going.
    data.activities[A] = [pending('k1')]
    await clock.tick()
    expect(source.listActivities).toHaveBeenCalledTimes(2)
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('pending_approval')
    expect(session.timeline.value.map((i) => i.key)).toEqual(['turn:1', 'turn:2', 'command:k1', 'status:' + session.state.activities[0].id])

    // The user approves; the same live run keeps being polled and gets its reply.
    await session.decideCommand('k1', 'approve')
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('approved')
    expect(session.state.runId).toBe('run-1')
    data.activities[A] = [{ ...pending('k1'), state: 'succeeded', exitCode: 0 }]
    await clock.tick()
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('succeeded')

    // Run ends: the reply arrives and one more read brings the run change.
    data.turns[A].push(turn(3, 'ai', 'build is green', '2026-10-06T08:10:00Z'))
    data.activities[A] = [{ ...pending('k1'), state: 'succeeded' }, runChange]
    const readsBefore = source.listActivities.mock.calls.length
    await clock.tick()
    expect(session.state.runId).toBeNull()
    expect(source.listActivities.mock.calls.length).toBe(readsBefore + 1)
    expect(keys(session.state.activities.filter((a) => a.kind !== 'status'))).toEqual(['command:k1', 'change:run-1'])
    expect(session.timeline.value.filter((i) => i.kind !== 'status').map((i) => i.key)).toEqual([
      'turn:1',
      'turn:2',
      'command:k1',
      'turn:3',
      'change:run-1',
    ])
  })

  it('re-reads on the ticks of an adopted run too (409 run_in_progress)', async () => {
    const clock = manualSleep()
    const { session, source, data } = setup({ runs: { 'run-9': ['running', 'finished'] } }, clock.sleep)
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(Object.assign(httpError(409, 'busy', 'run_in_progress'), { runId: 'run-9' }))
    await session.invokeAi()
    data.activities[A] = [pending('k1', { runId: 'run-9' })]
    await clock.tick()
    expect(commandOf(session.state.activities, 'k1')).toBeTruthy()
  })

  it('keeps what is shown when a read fails and reads again on the next tick', async () => {
    const clock = manualSleep()
    const { session, source, data } = setup({ activities: { [A]: [pending('k1')] }, runs: { 'run-1': ['running', 'running', 'finished'] } }, clock.sleep)
    source.startChatRun.mockResolvedValueOnce('run-1')
    await session.setScope(SCOPE)
    await flushPromises()
    await session.invokeAi()
    source.listActivities.mockRejectedValueOnce(httpError(500, 'down'))
    await clock.tick()
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('pending_approval')
    expect(session.state.notice).toBeNull()
    data.activities[A] = [{ ...pending('k1'), state: 'running' }]
    await clock.tick()
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('running')
  })

  it('restores the server state when the chat is opened again', async () => {
    const { session, source, data } = setup({ activities: { [A]: [pending('k1')] } })
    await session.setScope(SCOPE)
    await flushPromises()
    await session.selectChat(B)
    await flushPromises()
    expect(keys(session.state.activities)).toEqual([])
    data.activities[A] = [{ ...pending('k1'), state: 'running' }]
    await session.selectChat(A)
    await flushPromises()
    expect(source.listActivities).toHaveBeenLastCalledWith(A)
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('running')
  })

  it('drops a read the server no longer lists and keeps local status events', async () => {
    const { session, data } = setup({ activities: { [A]: [pending('k1'), pending('k2')] }, runs: { 'run-1': ['finished'] } })
    await session.setScope(SCOPE)
    await flushPromises()
    session.pushActivity({ kind: 'status', id: 's1', createdAt: null, tone: 'info', code: null, text: 'local' })
    data.activities[A] = [pending('k2')]
    await session.invokeAi()
    await flushPromises()
    expect(keys(session.state.activities).filter((k) => !k.startsWith('status:local-'))).toEqual(['status:s1', 'command:k2'])
  })
})

describe('command decision recovery (D0006 §6.2)', () => {
  async function opened(init: Parameters<typeof createFakeSource>[0] = {}) {
    const ctx = setup({ activities: { [A]: [pending('k1')] }, ...init })
    await ctx.session.setScope(SCOPE)
    await flushPromises()
    return ctx
  }

  it('success: the card becomes the answered request and the activity is read once more', async () => {
    const { session, source } = await opened()
    const reads = source.listActivities.mock.calls.length
    await session.decideCommand('k1', 'reject')
    expect(commandOf(session.state.activities, 'k1')).toMatchObject({ state: 'rejected', rejectedBy: 'user' })
    expect(source.listActivities.mock.calls.length).toBe(reads + 1)
  })

  it('shows approving / rejecting only while the call is in flight', async () => {
    const { session, source, data } = await opened()
    let release!: () => void
    source.decideCommand.mockImplementationOnce(
      () =>
        new Promise<CommandRequestActivity>(
          (resolve) =>
            (release = () => {
              data.activities[A] = [{ ...pending('k1'), state: 'approved' }]
              resolve({ ...pending('k1'), state: 'approved' })
            }),
        ),
    )
    const done = session.decideCommand('k1', 'approve')
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('approving')
    expect(session.state.deciding).toEqual(['k1'])
    // A second press while in flight sends nothing.
    await session.decideCommand('k1', 'approve')
    expect(source.decideCommand).toHaveBeenCalledTimes(1)
    release()
    await done
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('approved')
    expect(session.state.deciding).toEqual([])
  })

  it('409: puts the card back, shows the server state, says it was already handled and does not retry', async () => {
    const { session, source, data } = await opened()
    source.decideCommand.mockImplementationOnce(async () => {
      data.activities[A] = [{ ...pending('k1'), state: 'cancelled', errorCode: 'chat_command_run_finished' }]
      throw httpError(409, 'run finished', 'chat_command_run_finished')
    })
    await session.decideCommand('k1', 'approve')
    expect(commandOf(session.state.activities, 'k1')).toMatchObject({ state: 'cancelled', errorCode: 'chat_command_run_finished' })
    expect(session.state.notice).toMatchObject({ code: 'command_already_handled' })
    expect(source.decideCommand).toHaveBeenCalledTimes(1)
  })

  it('403: keeps the server state, hides the decision and keeps it hidden across reads', async () => {
    const { session, source } = await opened()
    source.decideCommand.mockRejectedValueOnce(httpError(403, 'not yours', 'chat_command_decision_forbidden'))
    await session.decideCommand('k1', 'approve')
    expect(commandOf(session.state.activities, 'k1')).toMatchObject({ state: 'pending_approval', decisionForbidden: true })
    expect(session.state.notice).toMatchObject({ code: 'command_decision_forbidden' })
    await session.decideCommand('k1', 'approve')
    expect(source.decideCommand).toHaveBeenCalledTimes(1)
  })

  it('404: reads again and lets the card go when the server no longer has it', async () => {
    const { session, source, data } = await opened()
    source.decideCommand.mockImplementationOnce(async () => {
      data.activities[A] = []
      throw httpError(404, 'gone', 'chat_command_not_found')
    })
    await session.decideCommand('k1', 'approve')
    expect(commandOf(session.state.activities, 'k1')).toBeUndefined()
    expect(session.state.notice).toBeNull()
  })

  it('network / 5xx: puts the card back to pending so it can be pressed again', async () => {
    const { session, source } = await opened()
    source.decideCommand.mockRejectedValueOnce(httpError(502, 'bad gateway'))
    await session.decideCommand('k1', 'approve')
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('pending_approval')
    expect(session.state.notice).toMatchObject({ code: 'command_decision_failed' })
    source.decideCommand.mockRejectedValueOnce(new (await import('@/features/conversation/api')).ConversationRequestError('offline', null, null))
    await session.decideCommand('k1', 'approve')
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('pending_approval')
    await session.decideCommand('k1', 'approve')
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('approved')
    expect(source.decideCommand).toHaveBeenCalledTimes(3)
  })

  it('drops a read that started before the decision answered (it may still say pending)', async () => {
    const clock = manualSleep()
    const { session, source } = setup({ activities: { [A]: [pending('k1')] }, runs: { 'run-1': ['running', 'running'] } }, clock.sleep)
    source.startChatRun.mockResolvedValueOnce('run-1')
    await session.setScope(SCOPE)
    await flushPromises()
    await session.invokeAi()
    // A polling tick's read is in flight and will answer with the old, pending state…
    let releaseStale!: () => void
    source.listActivities.mockImplementationOnce(() => new Promise<ConversationActivity[]>((resolve) => (releaseStale = () => resolve([pending('k1')]))))
    await clock.tick()
    expect(releaseStale).toBeTypeOf('function')
    // …the decision answers meanwhile; its own read is still on the way when the stale one lands.
    let releaseFresh!: () => void
    source.listActivities.mockImplementationOnce(
      () => new Promise<ConversationActivity[]>((resolve) => (releaseFresh = () => resolve([{ ...pending('k1'), state: 'running' }]))),
    )
    const deciding = session.decideCommand('k1', 'approve')
    await flushPromises()
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('approved')
    releaseStale()
    await flushPromises()
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('approved')
    releaseFresh()
    await deciding
    expect(commandOf(session.state.activities, 'k1')?.state).toBe('running')
  })

  it('does not decide a request that is not pending', async () => {
    const { session, source } = await opened({ activities: { [A]: [{ ...pending('k1'), state: 'running' }] } })
    await session.decideCommand('k1', 'approve')
    expect(source.decideCommand).not.toHaveBeenCalled()
  })
})

describe('new chat recovery (D0006 §6.3)', () => {
  it('adds nothing before the server answers, then reloads the list and opens the new chat', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    let release!: () => void
    const original = source.createChat.getMockImplementation()!
    source.createChat.mockImplementationOnce((scope: unknown, title: string) => new Promise((resolve) => (release = () => resolve(original(scope, title)))))
    const listCalls = source.listChats.mock.calls.length
    const done = session.createChat('New talk')
    expect(session.state.creatingChat).toBe(true)
    expect(session.state.chats.map((c) => c.title)).toEqual(['Auth bug', 'Git conflict'])
    release()
    expect(await done).toBe(true)
    expect(source.listChats.mock.calls.length).toBe(listCalls + 1)
    expect(session.state.chats[0].title).toBe('New talk')
    expect(session.state.activeChatId).toBe(session.state.chats[0].id)
    expect(session.state.creatingChat).toBe(false)
  })

  it('still opens the new chat when the list read fails', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.listChats.mockRejectedValueOnce(httpError(500, 'down'))
    expect(await session.createChat('New talk')).toBe(true)
    expect(session.state.activeChatId).toBe(session.state.chats[0].id)
    expect(session.state.chats[0].title).toBe('New talk')
  })

  it.each([
    [httpError(403, 'Forbidden'), 'create_forbidden'],
    [httpError(409, 'disposed', 'conversation_group_disposed'), 'create_group_disposed'],
    [httpError(409, 'retry', 'conversation_number_reservation_failed'), 'create_failed'],
    [httpError(422, 'bad title', 'conversation_invalid_request'), 'create_failed'],
    [new Error('Network Error'), 'create_failed'],
  ])('keeps the open chat, says why and does not retry (%s)', async (error, code) => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.createChat.mockRejectedValueOnce(error)
    expect(await session.createChat('x')).toBe(false)
    expect(session.state.notice).toMatchObject({ code })
    expect(session.state.activeChatId).toBe(A)
    expect(session.state.chats.map((c) => c.id)).toEqual([A, B])
    expect(source.createChat).toHaveBeenCalledTimes(1)
    expect(session.state.creatingChat).toBe(false)
  })

  it('creates a chat while an AI run is in flight', async () => {
    const clock = manualSleep()
    const { session, source } = setup({ runs: { 'run-1': ['running'] } }, clock.sleep)
    source.startChatRun.mockResolvedValueOnce('run-1')
    await session.setScope(SCOPE)
    await flushPromises()
    await session.invokeAi()
    expect(session.running.value).toBe(true)
    expect(await session.createChat('Side talk')).toBe(true)
    expect(source.createChat).toHaveBeenCalledWith(SCOPE, 'Side talk')
    expect(session.state.chats[0].title).toBe('Side talk')
  })
})

describe('run change file diff', () => {
  const diff = {
    runId: 'run-1',
    path: 'src/a.ts',
    status: 'M',
    old: { exists: true, binary: false, truncated: false, size: 2, content: 'a\n' },
    new: { exists: true, binary: false, truncated: false, size: 2, content: 'b\n' },
  }

  it('opens the stored diff of a file and closes it on a second press', async () => {
    const { session, source } = setup({ activities: { [A]: [runChange] }, diffs: { 'run-1\nsrc/a.ts': diff } })
    await session.setScope(SCOPE)
    await flushPromises()
    const opening = session.openChangeFile('run-1', 'src/a.ts')
    expect(session.state.fileDiff).toMatchObject({ runId: 'run-1', path: 'src/a.ts', status: 'loading' })
    await opening
    expect(source.fetchRunFileDiff).toHaveBeenCalledWith(A, 'run-1', 'src/a.ts')
    expect(session.state.fileDiff).toMatchObject({ status: 'ready', diff })
    await session.openChangeFile('run-1', 'src/a.ts')
    expect(session.state.fileDiff).toBeNull()
  })

  it('shows a failed read and drops an answer for a chat no longer open', async () => {
    const { session, source } = setup({ activities: { [A]: [runChange] } })
    await session.setScope(SCOPE)
    await flushPromises()
    await session.openChangeFile('run-1', 'missing.ts')
    expect(session.state.fileDiff).toMatchObject({ status: 'error' })
    let release!: () => void
    source.fetchRunFileDiff.mockImplementationOnce(() => new Promise((resolve) => (release = () => resolve(diff))))
    const opening = session.openChangeFile('run-1', 'src/a.ts')
    await session.selectChat(B)
    release()
    await opening
    expect(session.state.fileDiff).toBeNull()
  })
})
