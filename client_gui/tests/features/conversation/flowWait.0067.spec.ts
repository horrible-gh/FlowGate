import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18nPlugin from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationRequestError, DEFAULT_CHAT_SETTINGS, toTurn } from '@/features/conversation/api'
import { createConversationSession, type ConversationSession } from '@/features/conversation/conversationSession'
import { flowCalled, readFollowUps, withFlowWait, withoutFlowWait, writeFollowUps, type FollowUpMark } from '@/features/conversation/followUps'
import ConversationHeader from '@/features/conversation/ConversationHeader.vue'
import TimelineActivity from '@/features/conversation/TimelineActivity.vue'
import TimelineMessage from '@/features/conversation/TimelineMessage.vue'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'
import type { AiRunState } from '@/features/conversation/types'
import { createFakeSource, turn } from './fakeConversationSource'

// flowgate.v02test.0067 card 5 (#2, screen side): while a card of the Flow runs, an edit / edit-once / execute chat is
// refused by the server (409 graph_run_locked, or 409 run_in_progress of a run that is not the chat's). The screen
// no longer ends that call with an error: the message waits for the Flow («카드 실행이 끝나면 이어서 답합니다»), a
// paused run is waited through, and ONE call answers once the Flow's run is over. [중지] gives up the wait only.
// A read chat's answer written during a card run carries «카드 실행 중에 본 내용»; the header tells «플로우 실행 중»
// and «채팅 호출 중» apart.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0004-CH'

/** Every sleep of the session waits until the spec ticks; `tick()` lets each waiting sleep end once. */
function clock() {
  const waiters: Array<() => void> = []
  return {
    sleep: (_ms: number) => new Promise<void>((resolve) => waiters.push(resolve)),
    pending: () => waiters.length,
    tick: async () => {
      for (const resolve of waiters.splice(0)) resolve()
      for (let i = 0; i < 8; i++) await flushPromises()
    },
  }
}

const locked = () => new ConversationRequestError('locked', 409, 'graph_run_locked')

function setup(flowStatus: { value: string | null } = { value: 'running' }) {
  const c = clock()
  const fake = createFakeSource({
    chats: [
      { id: A, title: 'Chat', updatedAt: '2026-11-22T06:00:00Z' },
      { id: B, title: 'Other', updatedAt: '2026-11-22T05:00:00Z' },
    ],
    turns: { [A]: [turn(1, 'user', 'first question'), turn(2, 'ai', 'earlier answer', null, { basedOnSeq: 1 })], [B]: [] },
    settings: { ...DEFAULT_CHAT_SETTINGS, sendAction: 'invoke_ai', sourceAccessMode: 'edit' },
  })
  fake.source.fetchFlowRunStatus = vi.fn(async () => flowStatus.value)
  const session = track(createConversationSession({ source: fake.source, sleep: c.sleep }))
  return { ...fake, session, clock: c, flowStatus }
}

const sessions: ConversationSession[] = []
function track(session: ConversationSession): ConversationSession {
  sessions.push(session)
  return session
}

const statusLine = (s: ConversationSession, code: string) =>
  s.timeline.value.find((item) => item.kind === 'status' && item.activity.kind === 'status' && item.activity.code === code)
const waitingSeqs = (s: ConversationSession) =>
  s.timeline.value.filter((item) => item.kind === 'message' && 'waiting' in item && item.waiting).map((item) => item.kind === 'message' ? item.turn.seq : 0)
const errorLines = (s: ConversationSession) =>
  s.timeline.value.filter((item) => item.kind === 'status' && item.activity.kind === 'status' && item.activity.chatError)

beforeEach(() => {
  localStorage.clear()
})
afterEach(() => {
  for (const session of sessions.splice(0)) session.dispose()
})

describe('0067 #2: an edit chat while a card runs waits and answers by itself', () => {
  it('409 graph_run_locked: no error, «카드 실행이 끝나면 이어서 답합니다», then one call once the Flow run is over', async () => {
    const { session, source, clock: c, flowStatus } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(locked())

    expect(await session.send('add the run steps to README')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    // not an error: no notice, no recorded chat error (the source_locked line of before).
    expect(session.state.notice).toBeNull()
    expect(source.recordChatError).not.toHaveBeenCalled()
    expect(errorLines(session)).toEqual([])
    expect(session.state.runId).toBeNull()
    expect(session.followUp.value).toMatchObject({ chatId: A, reason: 'flow_run', waitingSeqs: [101], count: 1 })
    const line = statusLine(session, 'follow_up_flow_waiting')
    expect(line && line.kind === 'status' ? line.activity : null).toMatchObject({ code: 'follow_up_flow_waiting', text: '1' })
    expect(statusLine(session, 'follow_up_waiting')).toBeUndefined()
    expect(waitingSeqs(session)).toEqual([101])
    expect(session.flowRunning.value).toBe(true)

    // the Flow still runs, then pauses: the call keeps waiting (no new start).
    await c.tick()
    expect(source.fetchFlowRunStatus).toHaveBeenCalledWith(SCOPE.groupId)
    flowStatus.value = 'paused'
    await c.tick()
    flowStatus.value = 'stopping'
    await c.tick()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(session.followUp.value?.reason).toBe('flow_run')

    // the run is over: one call, on the message's AI; the line and 「대기」 go.
    flowStatus.value = 'idle'
    await c.tick()
    expect(source.startChatRun).toHaveBeenCalledTimes(2)
    expect(source.startChatRun.mock.calls[1][0]).toBe(A)
    expect(source.startChatRun.mock.calls[1][1]).toBe('opus')
    expect(session.state.runId).toBe('run-1')
    expect(session.followUp.value).toBeNull()
    expect(statusLine(session, 'follow_up_flow_waiting')).toBeUndefined()
    expect(waitingSeqs(session)).toEqual([])
    expect(session.flowRunning.value).toBe(false)
    // called for once: after that run nothing is called again.
    await c.tick()
    await c.tick()
    expect(source.startChatRun).toHaveBeenCalledTimes(2)
    expect(readFollowUps('guest', A)).toEqual([])
  })

  it('several messages while it waits: they join the wait and one call answers them (no start per message)', async () => {
    const { session, source, clock: c, flowStatus } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(locked())
    expect(await session.send('one')).toBe(true)
    await flushPromises()
    expect(await session.send('two')).toBe(true)
    session.selectProvider('sonnet')
    expect(await session.send('three')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(session.followUp.value).toMatchObject({ reason: 'flow_run', waitingSeqs: [101, 102, 103], count: 3 })

    flowStatus.value = null
    await c.tick()
    expect(source.startChatRun).toHaveBeenCalledTimes(2)
    // on the AI of the latest message.
    expect(source.startChatRun.mock.calls[1][1]).toBe('sonnet')
    expect(session.followUp.value).toBeNull()
  })

  it('refused again when it tries (the next card took the Flow): it waits on, no error, then answers', async () => {
    const { session, source, clock: c, flowStatus } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(locked()).mockRejectedValueOnce(locked())
    expect(await session.send('change the button')).toBe(true)
    await flushPromises()

    flowStatus.value = 'idle'
    await c.tick()
    expect(source.startChatRun).toHaveBeenCalledTimes(2)
    expect(session.followUp.value?.reason).toBe('flow_run')
    expect(errorLines(session)).toEqual([])
    expect(session.state.notice).toBeNull()

    await c.tick()
    expect(source.startChatRun).toHaveBeenCalledTimes(3)
    expect(session.state.runId).toBe('run-1')
    expect(session.followUp.value).toBeNull()
  })

  it('[중지] cancels the wait only: the message stays, no call follows and the card is not touched', async () => {
    const { session, source, clock: c, flowStatus } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(locked())
    expect(await session.send('add tests')).toBe(true)
    await flushPromises()
    expect(session.followUp.value?.reason).toBe('flow_run')

    expect(session.flowRunning.value).toBe(true)
    await session.stopRun()
    await flushPromises()
    expect(session.followUp.value).toBeNull()
    expect(session.flowRunning.value).toBe(false)
    expect(statusLine(session, 'follow_up_flow_waiting')).toBeUndefined()
    expect(readFollowUps('guest', A)).toEqual([])
    expect(session.state.turns.some((t) => t.seq === 101)).toBe(true)
    expect(source.cancelRun).not.toHaveBeenCalled()
    expect(source.cancelChatStart).toBeUndefined()

    flowStatus.value = 'idle'
    await c.tick()
    await c.tick()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
  })

  it('the line\'s [중지] (cancelFlowWait) does the same', async () => {
    const { session, source, clock: c, flowStatus } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(locked())
    expect(await session.send('add tests')).toBe(true)
    await flushPromises()
    session.cancelFlowWait()
    flowStatus.value = 'idle'
    await c.tick()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(session.followUp.value).toBeNull()
  })

  it('409 run_in_progress of a card\'s run (not this chat\'s): not followed as the chat\'s run, it waits for the slot', async () => {
    const { session, source, data, clock: c, flowStatus } = setup({ value: null })
    data.runDetails['card-run'] = {
      runId: 'card-run', status: 'running', endReason: null, docRef: 'p.m.0001.0010-T', actionScope: 'task', groupId: SCOPE.groupId,
    } as AiRunState
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(Object.assign(new ConversationRequestError('busy', 409, 'run_in_progress'), { runId: 'card-run' }))
    expect(await session.send('fix the typo')).toBe(true)
    await flushPromises()
    // first the run is looked at (it could be another tab's run of this chat) …
    expect(session.state.runId).toBe('card-run')
    await c.tick()
    // … it is a card's: no run line, no "result unconfirmed", the message waits for the Flow.
    expect(session.state.runId).toBeNull()
    expect(statusLine(session, 'run_result_unconfirmed')).toBeUndefined()
    expect(statusLine(session, 'run_running')).toBeUndefined()
    expect(session.followUp.value).toMatchObject({ reason: 'flow_run', waitingSeqs: [101] })
    expect(errorLines(session)).toEqual([])

    // the slot is free (the Flow has no locked run): one call.
    await c.tick()
    expect(source.startChatRun).toHaveBeenCalledTimes(2)
    expect(session.state.runId).toBe('run-1')
    expect(session.followUp.value).toBeNull()
  })

  it('409 run_in_progress of this chat\'s own run (another tab) is still followed as before', async () => {
    const { session, source, data, clock: c } = setup()
    data.runs['run-x'] = ['running', 'finished']
    data.replies['run-x'] = { chatId: A, turn: turn(102, 'ai', 'answered', null, { basedOnSeq: 101 }) }
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(Object.assign(new ConversationRequestError('busy', 409, 'run_in_progress'), { runId: 'run-x' }))
    expect(await session.send('question')).toBe(true)
    await flushPromises()
    await c.tick()
    expect(session.state.runId).toBe('run-x')
    expect(session.followUp.value?.reason).toBe('run')
    await c.tick()
    await c.tick()
    expect(session.state.runId).toBeNull()
    expect(source.fetchFlowRunStatus).not.toHaveBeenCalled()
  })

  it('F5 while it waits: the new screen shows the wait again and answers once the Flow run is over', async () => {
    const first = setup()
    await first.session.setScope(SCOPE)
    await flushPromises()
    first.source.startChatRun.mockRejectedValueOnce(locked())
    expect(await first.session.send('rename the file')).toBe(true)
    await flushPromises()
    first.session.dispose()

    // the reload: a new session on the same browser storage and server.
    const c = clock()
    const flow = { value: 'running' as string | null }
    first.source.fetchFlowRunStatus = vi.fn(async () => flow.value)
    const session = track(createConversationSession({ source: first.source, sleep: c.sleep }))
    await session.setScope(SCOPE)
    for (let i = 0; i < 8; i++) await flushPromises()
    expect(session.followUp.value).toMatchObject({ reason: 'flow_run', waitingSeqs: [101] })
    expect(waitingSeqs(session)).toEqual([101])
    expect(first.source.startChatRun).toHaveBeenCalledTimes(1)

    await c.tick()
    expect(first.source.startChatRun).toHaveBeenCalledTimes(1)
    flow.value = 'idle'
    await c.tick()
    expect(first.source.startChatRun).toHaveBeenCalledTimes(2)
    expect(session.followUp.value).toBeNull()
  })

  it('sent from another chat (off screen): the refusal waits for that chat, and opening it goes on waiting', async () => {
    const { session, source, clock: c, flowStatus } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    // the message leaves for A, then the screen moves to B before its AI call runs.
    let release: () => void = () => undefined
    source.postTurn.mockImplementationOnce(async (chatId: string, input: { body: string }) => {
      await new Promise<void>((resolve) => { release = resolve })
      const saved = turn(101, 'user', input.body, '2026-11-22T10:00:00Z')
      return { turn: saved, headSeq: 101 }
    })
    source.startChatRun.mockRejectedValueOnce(locked())
    const sent = session.send('edit while away')
    await flushPromises()
    await session.selectChat(B)
    await flushPromises()
    release()
    expect(await sent).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(source.startChatRun.mock.calls[0][0]).toBe(A)
    expect(source.recordChatError).not.toHaveBeenCalled()
    expect(readFollowUps('guest', A)).toMatchObject([{ seq: 101, flow: true }])
    // B waits for nothing.
    expect(session.followUp.value).toBeNull()

    await session.selectChat(A)
    for (let i = 0; i < 8; i++) await flushPromises()
    expect(session.followUp.value?.reason).toBe('flow_run')
    flowStatus.value = 'idle'
    await c.tick()
    expect(source.startChatRun).toHaveBeenCalledTimes(2)
    expect(source.startChatRun.mock.calls[1][0]).toBe(A)
  })

  it('the Designer\'s run lock going off wakes the wait at once', async () => {
    const { session, source, flowStatus } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(locked())
    expect(await session.send('go')).toBe(true)
    await flushPromises()
    flowStatus.value = 'idle'
    session.wakeFlowWait()
    for (let i = 0; i < 8; i++) await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledTimes(2)
  })

  it('a bare [AI 호출] refused for the Flow waits too (answers the chat as it stands)', async () => {
    const { session, source, clock: c, flowStatus } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(locked())
    await session.invokeAi()
    await flushPromises()
    expect(session.followUp.value).toMatchObject({ reason: 'flow_run', count: 0 })
    const line = statusLine(session, 'follow_up_flow_waiting')
    expect(line && line.kind === 'status' ? line.activity.text : 'x').toBeNull()
    flowStatus.value = 'idle'
    await c.tick()
    expect(source.startChatRun).toHaveBeenCalledTimes(2)
    expect(session.followUp.value).toBeNull()
  })

  it('another failure on the retry ends the wait with that error (no loop)', async () => {
    const { session, source, clock: c, flowStatus } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(locked()).mockRejectedValueOnce(new ConversationRequestError('bad', 500, 'internal'))
    expect(await session.send('go')).toBe(true)
    await flushPromises()
    flowStatus.value = 'idle'
    await c.tick()
    expect(source.startChatRun).toHaveBeenCalledTimes(2)
    expect(session.followUp.value).toBeNull()
    expect(readFollowUps('guest', A)).toEqual([])
    await c.tick()
    await c.tick()
    expect(source.startChatRun).toHaveBeenCalledTimes(2)
  })
})

describe('0067 #2: followUps helpers for the Flow wait', () => {
  const mark = (seq: number, extra: Partial<FollowUpMark> = {}): FollowUpMark => ({ seq, providerId: 'opus', at: Date.now(), ...extra })
  const turns = [turn(1, 'user', 'q'), turn(2, 'ai', 'a', null, { basedOnSeq: 1 }), turn(3, 'user', 'later')]

  it('withFlowWait: the called-for marks wait again, every waiting mark waits for the Flow, an answered one is left', () => {
    const next = withFlowWait(turns, [mark(1), mark(3, { auto: true })], mark(4))
    expect(next).toEqual([
      expect.objectContaining({ seq: 1 }),
      expect.objectContaining({ seq: 3, flow: true }),
      expect.objectContaining({ seq: 4, flow: true }),
    ])
    expect(next[0].flow).toBeUndefined()
    expect(next[1].auto).toBeUndefined()
  })

  it('flowCalled / withoutFlowWait, and the flag is kept in storage', () => {
    const marks = [mark(3, { flow: true }), mark(5)]
    expect(flowCalled(marks)).toEqual([expect.objectContaining({ seq: 3, auto: true }), expect.objectContaining({ seq: 5 })])
    expect(flowCalled(marks)[0].flow).toBeUndefined()
    expect(withoutFlowWait(marks).map((m) => m.seq)).toEqual([5])
    writeFollowUps('u1', A, marks)
    expect(readFollowUps('u1', A)).toEqual([expect.objectContaining({ seq: 3, flow: true }), expect.not.objectContaining({ flow: true })])
  })
})

describe('0067 #2: «카드 실행 중에 본 내용», the wait line and the header', () => {
  registerAppMessages(i18nPlugin)
  const i18n = () => {
    i18nPlugin.global.locale.value = 'ko'
    return i18nPlugin
  }

  it('the turn carries observed_during_card_run (an AI answer of a read chat during a card run)', () => {
    expect(toTurn({ seq: 5, speaker: 'ai', body: 'x', observed_during_card_run: true }).observedDuringCardRun).toBe(true)
    expect(toTurn({ seq: 5, speaker: 'ai', body: 'x', observed_during_card_run: false }).observedDuringCardRun).toBeUndefined()
    expect(toTurn({ seq: 5, speaker: 'ai', body: 'x' }).observedDuringCardRun).toBeUndefined()
  })

  it('an AI answer marked so shows the badge with its explanation; others do not', () => {
    const marked = mount(TimelineMessage, {
      props: { turn: turn(5, 'ai', 'still adding tests', '2026-11-22T01:41:00Z', { observedDuringCardRun: true, displayName: 'Opus' }) },
      global: { plugins: [i18n()] },
    })
    const badge = marked.find('[data-test="tl-seen-during-run"]')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('카드 실행 중에 본 내용')
    expect(badge.attributes('title')).toContain('카드가 고치는 중인 파일')
    const plain = mount(TimelineMessage, {
      props: { turn: turn(6, 'ai', 'answer', '2026-11-22T01:42:00Z') },
      global: { plugins: [i18n()] },
    })
    expect(plain.find('[data-test="tl-seen-during-run"]').exists()).toBe(false)
    const user = mount(TimelineMessage, {
      props: { turn: turn(7, 'user', 'q', '2026-11-22T01:42:00Z', { observedDuringCardRun: true }) },
      global: { plugins: [i18n()] },
    })
    expect(user.find('[data-test="tl-seen-during-run"]').exists()).toBe(false)
  })

  it('the wait line says «카드 실행이 끝나면 이어서 답합니다» with its count and a [중지] that emits cancel-wait', async () => {
    const wrapper = mount(TimelineActivity, {
      props: { activity: { kind: 'status', id: 'local-status-follow-up', chatId: A, createdAt: null, tone: 'info', code: 'follow_up_flow_waiting', text: '2' } as never },
      global: { plugins: [i18n()] },
    })
    expect(wrapper.text()).toContain('카드 실행이 끝나면 이어서 답합니다 · 기다리는 메시지 2개')
    expect(wrapper.find('[data-test="tl-flow-wait"]').exists()).toBe(true)
    // no copy button: it is no error line; no [지금 멈추고 다시 답하기]: there is no answer to stop.
    expect(wrapper.find('[data-test="tl-status-copy"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="tl-reply-now"]').exists()).toBe(false)
    await wrapper.find('[data-test="tl-flow-wait-cancel"]').trigger('click')
    expect(wrapper.emitted('cancel-wait')).toHaveLength(1)
  })

  it('a read-only screen has no [중지] on the wait line', () => {
    const wrapper = mount(TimelineActivity, {
      props: { readOnly: true, activity: { kind: 'status', id: 'x', chatId: A, createdAt: null, tone: 'info', code: 'follow_up_flow_waiting', text: null } as never },
      global: { plugins: [i18n()] },
    })
    expect(wrapper.text()).toContain('카드 실행이 끝나면 이어서 답합니다')
    expect(wrapper.find('[data-test="tl-flow-wait-cancel"]').exists()).toBe(false)
  })

  it('the header tells «플로우 실행 중» and «채팅 호출 중» apart', () => {
    const base = { chats: [], chatsStatus: 'ready', activeChat: null, hasScope: true, creating: false, renaming: false, title: 'Chat' }
    const both = mount(ConversationHeader, { props: { ...base, flowRunning: true, chatRunning: true }, global: { plugins: [i18n()] } })
    expect(both.find('[data-test="conv-flow-running"]').text()).toBe('플로우 실행 중')
    expect(both.find('[data-test="conv-chat-running"]').text()).toBe('채팅 호출 중')
    const flowOnly = mount(ConversationHeader, { props: { ...base, flowRunning: true, chatRunning: false }, global: { plugins: [i18n()] } })
    expect(flowOnly.find('[data-test="conv-flow-running"]').exists()).toBe(true)
    expect(flowOnly.find('[data-test="conv-chat-running"]').exists()).toBe(false)
    const none = mount(ConversationHeader, { props: base, global: { plugins: [i18n()] } })
    expect(none.find('[data-test="conv-flow-running"]').exists()).toBe(false)
    expect(none.find('[data-test="conv-chat-running"]').exists()).toBe(false)
  })

  it('the new words exist in ko / en / ja', () => {
    for (const messages of [ko, en, ja] as Array<Record<string, any>>) {
      const conv = messages.conversation
      expect(conv.status.follow_up_flow_waiting).toBeTruthy()
      expect(conv.status.follow_up_flow_cancel).toBeTruthy()
      expect(conv.status.follow_up_flow_cancel_title).toBeTruthy()
      expect(conv.message.seen_during_card_run).toBeTruthy()
      expect(conv.message.seen_during_card_run_title).toBeTruthy()
      expect(conv.header.flow_running).toBeTruthy()
      expect(conv.header.chat_running).toBeTruthy()
    }
  })
})
