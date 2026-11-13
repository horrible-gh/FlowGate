import { describe, expect, it } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createChatStart } from '@/features/conversation/chatStart'
import { createConversationSession } from '@/features/conversation/conversationSession'
import { createSubmissionTrace, traceRecord } from '@/features/conversation/submissionTrace'
import { draftUserId, type DraftSendSnapshot } from '@/features/conversation/drafts'
import { createFakeSource, httpError } from './fakeConversationSource'

const scope = { projectId: 'p', groupId: 'p.m.0001' }
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
async function setup() {
  const fake = createFakeSource()
  const session = createConversationSession({ source: fake.source })
  const start = createChatStart({ session, source: fake.source, defaultTitle: () => 'new' })
  await session.setScope(scope)
  return { ...fake, session, start }
}

function snapshot(start: ReturnType<typeof createChatStart>, chatId: string | null, body = 'keep', revision = 1): DraftSendSnapshot {
  return { owner: { ...scope, userId: draftUserId(), intentId: start.draftIntentId.value!, chatId }, body, revision }
}

describe('0141 first submission boundary', () => {
  it('sets creating synchronously and sends before a delayed list response', async () => {
    const { source, session, start } = await setup()
    const create = deferred<{ id: string; title: string; updatedAt: null }>()
    const list = deferred<[]>()
    source.createChat.mockImplementationOnce(() => create.promise)
    source.listChats.mockImplementationOnce(() => list.promise)
    const sending = start.send('one')
    expect(start.phase.value?.phase).toBe('creating')
    expect(await start.send('one')).toBe(false)
    create.resolve({ id: 'p.m.0001.0010-CH', title: 'new', updatedAt: null })
    expect(await sending).toBe(true)
    expect(source.postTurn).toHaveBeenCalledTimes(1)
    list.resolve([])
    await flushPromises()
    expect(session.state.chats.some(c => c.id === 'p.m.0001.0010-CH')).toBe(true)
    session.dispose()
  })

  it('shows history failure as load and never posts with an unknown head', async () => {
    const { source, session, start } = await setup()
    source.fetchTurns.mockRejectedValueOnce(httpError(503, 'history'))
    expect(await start.send('keep')).toBe(false)
    expect(start.phase.value).toMatchObject({ phase: 'failed', stage: 'load' })
    expect(source.postTurn).not.toHaveBeenCalled()
    expect(session.state.activeChatId).not.toBeNull()
    session.dispose()
  })

  it('keeps the replay key for both composer resubmission and bubble retry', async () => {
    const { source, session, start } = await setup()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'send'))
    const original = snapshot(start, null)
    expect(await start.send('keep', original)).toBe(false)
    const item = session.state.pending[0]!
    expect(await start.retry(item.localId)).toBe(true)
    const posted = source.postTurn.mock.calls.length
    expect(await start.send('keep', { ...original, owner: { ...original.owner, chatId: session.state.activeChatId } })).toBe(true)
    expect(source.postTurn.mock.calls.length).toBe(posted)
    expect(source.postTurn.mock.calls[1][1].idempotencyKey).toBe(item.idempotencyKey)
    session.dispose()
  })

  it('flowgate.v02gui.0009: finishes an unsent continuation in its own chat after a Flow switch', async () => {
    const { source, session, start } = await setup()
    const history = deferred<any>()
    source.fetchTurns.mockImplementationOnce(() => history.promise)
    const sending = start.send('A')
    await flushPromises()
    expect(start.phase.value?.phase).toBe('loading')
    await session.setScope({ projectId: 'p', groupId: 'p.m.0002' })
    history.resolve({ turns: [], headSeq: 0, participants: [], nextAfterSeq: null, prevBeforeSeq: null })
    expect(await sending).toBe(true)
    expect(source.postTurn).toHaveBeenCalledTimes(1)
    expect(source.postTurn).toHaveBeenCalledWith('p.m.0001.0010-CH', expect.objectContaining({ body: 'A', basedOnSeq: 0 }))
    expect(start.phase.value).toBeNull()
    session.dispose()
  })

  it('posts newly retyped identical text after a successful bubble retry', async () => {
    const { source, session, start } = await setup()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'send'))
    const original = snapshot(start, null, '계속', 1)
    expect(await start.send('계속', original)).toBe(false)
    const item = session.state.pending[0]!
    expect(await start.retry(item.localId)).toBe(true)
    // Deleting and retyping increments the composer's revision even for equal text.
    const retyped = snapshot(start, session.state.activeChatId, '계속', 3)
    expect(await start.send('계속', retyped)).toBe(true)
    expect(source.postTurn).toHaveBeenCalledTimes(3)
    expect(source.postTurn.mock.calls[1][1].idempotencyKey).toBe(item.idempotencyKey)
    expect(source.postTurn.mock.calls[2][1].idempotencyKey).not.toBe(item.idempotencyKey)
    session.dispose()
  })

  it('does not consume a new draft with the same body or a submission without a snapshot', async () => {
    for (const withSnapshot of [false, true]) {
      const { source, session, start } = await setup()
      source.postTurn.mockRejectedValueOnce(httpError(422, 'send'))
      const original = snapshot(start, null)
      expect(await start.send('keep', original)).toBe(false)
      expect(await start.retry(session.state.pending[0]!.localId)).toBe(true)
      const other = snapshot(start, session.state.activeChatId)
      other.owner.intentId += '-new'
      // Session callers without a draft identity must never receive a text-only receipt.
      expect(await session.send('keep', undefined, undefined, withSnapshot ? other : undefined)).toBe(true)
      expect(source.postTurn).toHaveBeenCalledTimes(3)
      session.dispose()
    }
  })

  it('acknowledges only the unchanged original draft after reopening a chat', async () => {
    const { source, session, start } = await setup()
    source.postTurn.mockRejectedValueOnce(httpError(422, 'send'))
    const original = snapshot(start, null)
    expect(await start.send('keep', original)).toBe(false)
    const chatId = session.state.activeChatId!
    expect(await start.retry(session.state.pending[0]!.localId)).toBe(true)
    session.startDraft()
    await session.selectChat(chatId)
    expect(await start.send('keep', snapshot(start, chatId))).toBe(true)
    expect(source.postTurn).toHaveBeenCalledTimes(2)
    expect(await start.send('keep', snapshot(start, chatId, 'keep', 3))).toBe(true)
    expect(source.postTurn).toHaveBeenCalledTimes(3)
    session.dispose()
  })

  it('clears failed_load after a successful history reload without another send', async () => {
    const { source, session, start } = await setup()
    source.fetchTurns.mockRejectedValueOnce(httpError(503, 'history'))
    expect(await start.send('keep')).toBe(false)
    expect(start.phase.value).toMatchObject({ phase: 'failed', stage: 'load' })
    source.fetchTurns.mockRejectedValueOnce(httpError(503, 'history again'))
    await session.selectChat(session.state.activeChatId!)
    expect(start.phase.value).toMatchObject({ phase: 'failed', stage: 'load' })
    await session.selectChat(session.state.activeChatId!)
    expect(session.state.turnsStatus).toBe('ready')
    expect(start.phase.value).toBeNull()
    expect(source.postTurn).not.toHaveBeenCalled()
    session.dispose()
  })

  it('discards settled create and load failures across a Flow round trip', async () => {
    for (const stage of ['create', 'load']) {
      const { source, session, start } = await setup()
      if (stage === 'create') source.createChat.mockRejectedValueOnce(httpError(422, 'create'))
      else source.fetchTurns.mockRejectedValueOnce(httpError(503, 'history'))
      expect(await start.send('keep')).toBe(false)
      expect(start.phase.value).toMatchObject({ phase: 'failed', stage })
      await session.setScope({ projectId: 'p', groupId: 'p.m.0002' })
      expect(start.phase.value).toBeNull()
      await session.setScope(scope)
      expect(start.phase.value).toBeNull()
      expect(source.postTurn).not.toHaveBeenCalled()
      session.dispose()
    }
  })

  it('uses a pure fixed-field timing record and absorbs diagnostic sink failure', () => {
    const record = traceRecord('ct-test', 'turn_end', 2, 3, 5, { attempt: 2 })
    expect(record).toMatchObject({ ms: 3, deltaMs: 2, attempt: 2 })
    expect(traceRecord('Authorization secret', 'turn_end', 0, 0, 1).traceId).toBe('ct-invalid')
    expect(() => createSubmissionTrace(() => { throw Error('sink') }).mark('turn_start')).not.toThrow()
  })
})
