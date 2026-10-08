import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { createConversationSession } from '@/features/conversation/conversationSession'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02.0031 — Conversation session rules with an in-memory source.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0004-CH'

// The default sleep resolves at once, so a run polls to its end within one flush; `hold`
// keeps a run in flight for the specs that look at it mid-run.
const hold = () => new Promise<void>(() => undefined)

function setup(init: Parameters<typeof createFakeSource>[0] = {}, sleep: (ms: number) => Promise<void> = async () => undefined) {
  const fake = createFakeSource({
    chats: [
      { id: A, title: 'Auth bug', updatedAt: '2026-10-06T08:00:00Z' },
      { id: B, title: 'Git conflict', updatedAt: '2026-10-05T08:00:00Z' },
    ],
    turns: {
      [A]: [turn(1, 'user', 'look', '2026-10-06T08:02:00Z'), turn(2, 'ai', 'done', '2026-10-06T08:03:00Z', { displayName: 'Opus' })],
      [B]: [turn(1, 'user', 'other chat', '2026-10-05T08:00:00Z')],
    },
    ...init,
  })
  const onAutoCopyMention = vi.fn()
  const session = createConversationSession({ source: fake.source, sleep, onAutoCopyMention })
  return { ...fake, session, onAutoCopyMention }
}

describe('scope and chat list', () => {
  it('loads the Flow chats, opens the newest and loads providers and settings', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    expect(source.listChats).toHaveBeenCalledWith(SCOPE)
    expect(session.state.activeChatId).toBe(A)
    expect(session.state.turns.map((t) => t.seq)).toEqual([1, 2])
    expect(session.state.selectedProviderId).toBe('opus')
    expect(source.fetchChatSettings).toHaveBeenCalled()
  })

  it('switches chats and drops a late answer of the previous one', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    let release!: () => void
    source.fetchTurns.mockImplementationOnce(
      () => new Promise((resolve) => (release = () => resolve({ turns: [turn(9, 'user', 'late')], participants: [], myReadSeq: null, headSeq: 9, nextAfterSeq: null, prevBeforeSeq: null }))),
    )
    const slow = session.selectChat(B)
    const fast = session.selectChat(A)
    await fast
    release()
    await slow
    expect(session.state.activeChatId).toBe(A)
    expect(session.state.turns.map((t) => t.body)).toEqual(['look', 'done'])
  })

  it('clears everything when the scope goes away', async () => {
    const { session } = setup()
    await session.setScope(SCOPE)
    await session.setScope(null)
    expect(session.state.chats).toEqual([])
    expect(session.state.activeChatId).toBeNull()
    expect(session.timeline.value).toEqual([])
  })

  it('renames the active chat and keeps the old title on failure', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    expect(await session.renameActiveChat('  Renamed  ')).toBe(true)
    expect(source.renameChat).toHaveBeenCalledWith(A, 'Renamed')
    expect(session.activeChat.value?.title).toBe('Renamed')
    source.renameChat.mockRejectedValueOnce(httpError(403, 'no permission'))
    expect(await session.renameActiveChat('Other')).toBe(false)
    expect(session.activeChat.value?.title).toBe('Renamed')
    expect(session.state.notice).toMatchObject({ tone: 'danger', code: 'rename_failed', detail: 'no permission' })
  })

  it('creates a chat, puts it first and opens it', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    expect(await session.createChat('New talk')).toBe(true)
    expect(source.createChat).toHaveBeenCalledWith(SCOPE, 'New talk')
    expect(session.state.chats[0].title).toBe('New talk')
    expect(session.state.activeChatId).toBe(session.state.chats[0].id)
    expect(session.state.turns).toEqual([])
  })

  it('reports a refused new chat inline', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    source.createChat.mockRejectedValueOnce(httpError(409, 'Current next step is T. Requested type: CH'))
    expect(await session.createChat('x')).toBe(false)
    expect(session.state.notice).toMatchObject({ code: 'create_failed', detail: 'Current next step is T. Requested type: CH' })
    expect(session.state.activeChatId).toBe(A)
  })
})

describe('timeline', () => {
  it('merges command requests and work changes into the message flow at their server anchors', async () => {
    const { session } = setup({
      activities: {
        [A]: [
          { kind: 'command', id: 'k1', createdAt: null, command: 'git diff', state: 'pending_approval', anchor: { seq: 2, position: 'before', state: 'reply' } },
          {
            kind: 'change',
            id: 'c1',
            runId: 'c1',
            createdAt: null,
            files: [{ path: 'a.ts', status: 'M', insertions: 3, deletions: 1 }],
            filesChanged: 1,
            insertions: 3,
            deletions: 1,
            anchor: { seq: 2, position: 'after', state: 'reply' },
          },
        ],
      },
    })
    await session.setScope(SCOPE)
    await flushPromises()
    expect(session.timeline.value.map((i) => i.key)).toEqual(['turn:1', 'command:k1', 'turn:2', 'change:c1'])
    await session.send('next')
    // The new message goes after the work change: the change flows up, it is not pinned.
    expect(session.timeline.value.map((i) => i.kind)).toEqual(['message', 'command', 'message', 'change', 'message'])
  })

  it('takes pushed activities', async () => {
    const { session } = setup()
    await session.setScope(SCOPE)
    session.pushActivity({ kind: 'status', id: 's', createdAt: '2026-10-06T09:00:00Z', tone: 'info', code: null, text: 'hello' })
    expect(session.timeline.value.at(-1)?.key).toBe('status:s')
  })
})

describe('send', () => {
  it('shows the message at once and replaces it with the numbered turn', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    const sending = session.send('  hi  ')
    expect(session.state.pending).toHaveLength(1)
    expect(session.timeline.value.at(-1)).toMatchObject({ kind: 'message', pending: true })
    expect(await sending).toBe(true)
    expect(source.postTurn).toHaveBeenCalledWith(A, expect.objectContaining({ body: 'hi', basedOnSeq: 2 }))
    expect(session.state.pending).toEqual([])
    expect(session.state.turns.at(-1)?.body).toBe('hi')
  })

  it('keeps a failed message with a retry that reuses the same key', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    source.postTurn.mockRejectedValueOnce(httpError(422, 'corrupted body'))
    expect(await session.send('hi')).toBe(false)
    expect(source.postTurn).toHaveBeenCalledTimes(1)
    const failed = session.state.pending[0]
    expect(failed.failed).toBe(true)
    expect(session.state.notice).toMatchObject({ code: 'send_failed', detail: 'corrupted body' })
    expect(await session.retry(failed.localId)).toBe(true)
    expect(source.postTurn.mock.calls[1][1].idempotencyKey).toBe(failed.idempotencyKey)
    expect(session.state.pending).toEqual([])
  })

  it('retries a transient failure itself', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    source.postTurn.mockRejectedValueOnce(httpError(503))
    expect(await session.send('hi')).toBe(true)
    expect(source.postTurn).toHaveBeenCalledTimes(2)
  })

  it('runs the send action after an accepted send', async () => {
    const copy = setup({ settings: { ...createFakeSource().data.settings, sendAction: 'copy_mention' } })
    await copy.session.setScope(SCOPE)
    await flushPromises()
    await copy.session.send('a')
    expect(copy.onAutoCopyMention).toHaveBeenCalledTimes(1)

    const invoke = setup({ settings: { ...createFakeSource().data.settings, sendAction: 'invoke_ai' } })
    await invoke.session.setScope(SCOPE)
    await flushPromises()
    await invoke.session.send('a')
    await flushPromises()
    expect(invoke.source.startChatRun).toHaveBeenCalledWith(A, 'opus')
  })

  it('reports read positions forward only', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await session.reportRead(2)
    await session.reportRead(1)
    expect(source.markRead).toHaveBeenCalledTimes(1)
    expect(session.state.readSeq).toBe(2)
  })

  it('keeps the reported read position per chat', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await session.reportRead(2)
    await session.selectChat(B)
    // seq 1 of chat B is not held back by seq 2 already reported for chat A.
    await session.reportRead(1)
    expect(source.markRead).toHaveBeenLastCalledWith(B, 1)
    expect(session.state.readSeq).toBe(1)
  })

  it('drops a read answer that comes back after a chat switch', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    let release!: () => void
    source.markRead.mockImplementationOnce(() => new Promise((resolve) => (release = () => resolve(2))))
    const late = session.reportRead(2)
    await session.selectChat(B)
    release()
    await late
    expect(session.state.activeChatId).toBe(B)
    expect(session.state.readSeq).toBe(0)
    await session.reportRead(1)
    expect(source.markRead).toHaveBeenLastCalledWith(B, 1)
  })

  it('drops the send action when the chat changed before the send was accepted', async () => {
    for (const sendAction of ['copy_mention', 'invoke_ai'] as const) {
      const { session, source, onAutoCopyMention } = setup({ settings: { ...createFakeSource().data.settings, sendAction } })
      await session.setScope(SCOPE)
      await flushPromises()
      let release!: () => void
      source.postTurn.mockImplementationOnce(() => new Promise((resolve) => (release = () => resolve({ turn: turn(3, 'user', 'a'), headSeq: 3 }))))
      const sending = session.send('a')
      await session.selectChat(B)
      release()
      expect(await sending).toBe(true)
      await flushPromises()
      expect(source.postTurn).toHaveBeenCalledWith(A, expect.objectContaining({ body: 'a' }))
      // Neither a mention nor an AI run lands in chat B, where no turn was sent.
      expect(onAutoCopyMention).not.toHaveBeenCalled()
      expect(source.startChatRun).not.toHaveBeenCalled()
      expect(session.state.turns.map((t) => t.body)).toEqual(['other chat'])
    }
  })

  it('runs the send action once after an accepted retry', async () => {
    const copy = setup({ settings: { ...createFakeSource().data.settings, sendAction: 'copy_mention' } })
    await copy.session.setScope(SCOPE)
    await flushPromises()
    copy.source.postTurn.mockRejectedValueOnce(httpError(422, 'bad'))
    expect(await copy.session.send('a')).toBe(false)
    expect(copy.onAutoCopyMention).not.toHaveBeenCalled()
    expect(await copy.session.retry(copy.session.state.pending[0].localId)).toBe(true)
    expect(copy.onAutoCopyMention).toHaveBeenCalledTimes(1)

    const invoke = setup({ settings: { ...createFakeSource().data.settings, sendAction: 'invoke_ai' } })
    await invoke.session.setScope(SCOPE)
    await flushPromises()
    invoke.source.postTurn.mockRejectedValueOnce(httpError(422, 'bad'))
    expect(await invoke.session.send('a')).toBe(false)
    expect(invoke.source.startChatRun).not.toHaveBeenCalled()
    expect(await invoke.session.retry(invoke.session.state.pending[0].localId)).toBe(true)
    await flushPromises()
    expect(invoke.source.startChatRun).toHaveBeenCalledTimes(1)
    expect(invoke.source.startChatRun).toHaveBeenCalledWith(A, 'opus')
  })

  it('drops the retry send action when the chat changed before the retry was accepted', async () => {
    const { session, source, onAutoCopyMention } = setup({ settings: { ...createFakeSource().data.settings, sendAction: 'copy_mention' } })
    await session.setScope(SCOPE)
    await flushPromises()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'bad'))
    await session.send('a')
    let release!: () => void
    source.postTurn.mockImplementationOnce(() => new Promise((resolve) => (release = () => resolve({ turn: turn(3, 'user', 'a'), headSeq: 3 }))))
    const retrying = session.retry(session.state.pending[0].localId)
    await session.selectChat(B)
    release()
    expect(await retrying).toBe(true)
    expect(onAutoCopyMention).not.toHaveBeenCalled()
  })
})

describe('command requests', () => {
  it('approves and rejects through the source', async () => {
    const { session, source } = setup({
      activities: {
        [A]: [
          { kind: 'command', id: 'k1', createdAt: null, command: 'git diff', state: 'pending_approval' },
          { kind: 'command', id: 'k2', createdAt: null, command: 'rm -rf', state: 'pending_approval' },
        ],
      },
    })
    await session.setScope(SCOPE)
    await flushPromises()
    await session.decideCommand('k1', 'approve')
    await session.decideCommand('k2', 'reject')
    expect(source.decideCommand).toHaveBeenCalledWith(A, 'k1', 'approve')
    expect(source.decideCommand).toHaveBeenCalledWith(A, 'k2', 'reject')
    const states = session.state.activities.map((a) => (a.kind === 'command' ? a.state : null))
    expect(states).toEqual(['approved', 'rejected'])
    // A decided request takes no second decision.
    await session.decideCommand('k1', 'reject')
    expect(source.decideCommand).toHaveBeenCalledTimes(2)
  })

  it('puts a request back to pending when the decision fails', async () => {
    const { session, source } = setup({ activities: { [A]: [{ kind: 'command', id: 'k1', createdAt: null, command: 'x', state: 'pending_approval' }] } })
    await session.setScope(SCOPE)
    await flushPromises()
    source.decideCommand.mockRejectedValueOnce(httpError(500, 'boom'))
    await session.decideCommand('k1', 'approve')
    expect(session.state.activities[0]).toMatchObject({ state: 'pending_approval' })
    expect(session.state.notice).toMatchObject({ code: 'command_decision_failed' })
  })
})

describe('AI run', () => {
  it('runs and picks up the reply without start / finish lines in the timeline', async () => {
    const { session, source, data } = setup()
    data.replies['run-1'] = { chatId: A, turn: turn(3, 'ai', 'reply', '2026-10-06T08:10:00Z') }
    await session.setScope(SCOPE)
    await flushPromises()
    session.selectProvider('sonnet')
    await session.invokeAi()
    expect(source.startChatRun).toHaveBeenCalledWith(A, 'sonnet')
    await flushPromises()
    expect(session.running.value).toBe(false)
    expect(session.state.turns.at(-1)?.body).toBe('reply')
    const statuses = session.state.activities.filter((a) => a.kind === 'status').map((a) => (a.kind === 'status' ? a.code : null))
    // flowgate.v02.0047 rev3: the reply itself is the news; no run_started / run_finished.
    expect(statuses).toEqual([])
    // A delivered answer re-reads the settings (an edit_once mode may have been used up).
    expect(source.fetchChatSettings.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('adopts the run that already owns the group (409 run_in_progress)', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(Object.assign(httpError(409, 'busy', 'run_in_progress'), { runId: 'run-x' }))
    await session.invokeAi()
    await flushPromises()
    expect(source.fetchRun).toHaveBeenCalledWith('run-x')
  })

  it('re-attaches to a chat run still running when the chat opens', async () => {
    const { session, source } = setup({ activeRun: 'run-live', runs: { 'run-live': ['running'] } }, hold)
    await session.setScope(SCOPE)
    await flushPromises()
    expect(source.fetchActiveChatRun).toHaveBeenCalledWith(A)
    expect(session.state.runId).toBe('run-live')
    await session.stopRun()
    expect(source.cancelRun).toHaveBeenCalledWith('run-live')
    expect(session.state.cancelling).toBe(true)
  })

  it('blocks sending while a run is in flight and warns without providers', async () => {
    const { session, source } = setup({ activeRun: 'run-live', runs: { 'run-live': ['running'] } }, hold)
    await session.setScope(SCOPE)
    await flushPromises()
    expect(await session.send('x')).toBe(false)
    expect(source.postTurn).not.toHaveBeenCalled()

    const none = setup()
    none.source.listProviders.mockResolvedValue({ providers: [], defaultProviderId: null })
    await none.session.setScope(SCOPE)
    await flushPromises()
    await none.session.invokeAi()
    expect(none.source.startChatRun).not.toHaveBeenCalled()
    expect(none.session.state.notice).toMatchObject({ code: 'no_provider' })
  })
})

describe('settings and mention', () => {
  it('moves the source mode only after the server confirms it', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.saveChatSettings.mockRejectedValueOnce(httpError(500, 'nope'))
    await session.setSourceMode('edit')
    expect(session.state.settings.sourceAccessMode).toBe('read_only')
    await session.setSourceMode('edit')
    expect(source.saveChatSettings).toHaveBeenLastCalledWith({ source_access_mode: 'edit' })
    expect(session.state.settings.sourceAccessMode).toBe('edit')
  })

  it('issues the chat mention of the active chat', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    expect(await session.issueMention()).toBe(`@mention ${A}`)
    expect(source.issueChatMention).toHaveBeenCalledWith(A)
  })
})
