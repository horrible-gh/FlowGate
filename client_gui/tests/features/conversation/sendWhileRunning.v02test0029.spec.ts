import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DEFAULT_CHAT_SETTINGS } from '@/features/conversation/api'
import ConversationComposer from '@/features/conversation/ConversationComposer.vue'
import { createConversationSession } from '@/features/conversation/conversationSession'
import { createFakeSource, turn } from './fakeConversationSource'

registerAppMessages(i18n)

// flowgate.v02test.0029 — "Chat while an answer is in progress": a message sent while the chat's AI answers is stored at once and shown as
// 대기; its AI call is not started next to the running one but once that run has ended (one follow-up per chat), unless
// the answer already read it.
const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'

/** A sleep the spec opens: the run's poll waits until `open()`. */
function gate() {
  let opened = false
  const waiters: Array<() => void> = []
  return {
    sleep: (_ms: number) => (opened ? Promise.resolve() : new Promise<void>((resolve) => waiters.push(resolve))),
    open: () => {
      opened = true
      for (const resolve of waiters.splice(0)) resolve()
    },
  }
}

function setup(extra: Parameters<typeof createFakeSource>[0] = {}) {
  const g = gate()
  const fake = createFakeSource({
    chats: [{ id: A, title: 'Chat', updatedAt: '2026-11-17T06:00:00Z' }],
    turns: { [A]: [turn(1, 'user', 'first question'), turn(2, 'ai', 'earlier answer', null, { basedOnSeq: 1 })] },
    settings: { ...DEFAULT_CHAT_SETTINGS, sendAction: 'invoke_ai' },
    activeRun: 'run-live',
    runs: { 'run-live': ['running', 'finished'] },
    ...extra,
  })
  const session = createConversationSession({ source: fake.source, sleep: g.sleep })
  return { ...fake, session, open: g.open }
}

describe('v02test.0029: sending while the AI answers', () => {
  it('stores the message at once, marks it 대기 and starts no second run while the first runs', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    expect(session.state.runId).toBe('run-live')

    expect(await session.send('one more thing')).toBe(true)
    await flushPromises()
    expect(source.postTurn).toHaveBeenCalledTimes(1)
    expect(source.startChatRun).not.toHaveBeenCalled()
    const sent = session.timeline.value.find((item) => item.kind === 'message' && item.turn.body === 'one more thing')
    expect(sent).toMatchObject({ kind: 'message', pending: false, waiting: true })
    // The earlier messages were seen by the run; they do not wait.
    const first = session.timeline.value.find((item) => item.kind === 'message' && item.turn.seq === 1)
    expect(first && 'waiting' in first ? first.waiting : undefined).toBeUndefined()

    // A second message while the same run goes on: still one follow-up, on the AI of the latest message.
    session.selectProvider('sonnet')
    expect(await session.send('and this')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(session.timeline.value.filter((item) => item.kind === 'message' && 'waiting' in item && item.waiting)).toHaveLength(2)
  })

  it('starts one follow-up call once the run has ended, then the 대기 marks go', async () => {
    const { session, source, data, open } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    data.activeRun = null
    session.selectProvider('sonnet')
    expect(await session.send('one more thing')).toBe(true)
    expect(await session.send('and this')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).not.toHaveBeenCalled()

    open()
    await flushPromises()
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(source.startChatRun.mock.calls[0][0]).toBe(A)
    expect(source.startChatRun.mock.calls[0][1]).toBe('sonnet')
    expect(session.timeline.value.some((item) => item.kind === 'message' && 'waiting' in item && item.waiting)).toBe(false)
  })

  it('starts no follow-up when the answer already read the waiting message', async () => {
    // postTurn numbers the first sent message 101; the run's answer (102) was written having read it.
    const { session, source, data, open } = setup({
      replies: { 'run-live': { chatId: A, turn: turn(102, 'ai', 'answered both', null, { basedOnSeq: 101 }) } },
    })
    await session.setScope(SCOPE)
    await flushPromises()
    data.activeRun = null
    expect(await session.send('one more thing')).toBe(true)
    await flushPromises()

    open()
    await flushPromises()
    await flushPromises()
    expect(session.state.runId).toBeNull()
    expect(source.startChatRun).not.toHaveBeenCalled()
  })

  it('a message sent without a run starts its call at once, as before', async () => {
    const { session, source } = setup({ activeRun: null, runs: {} })
    await session.setScope(SCOPE)
    await flushPromises()
    expect(await session.send('hello')).toBe(true)
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
  })

  it('leaving the chat drops the follow-up (the message stays stored)', async () => {
    const { session, source, data, open } = setup({
      chats: [
        { id: A, title: 'Chat', updatedAt: '2026-11-17T06:00:00Z' },
        { id: 'p.m.0001.0004-CH', title: 'Other', updatedAt: '2026-11-17T05:00:00Z' },
      ],
    })
    await session.setScope(SCOPE)
    await flushPromises()
    data.activeRun = null
    expect(await session.send('one more thing')).toBe(true)
    await flushPromises()
    await session.selectChat('p.m.0001.0004-CH')
    open()
    await flushPromises()
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(data.turns[A].some((t) => t.body === 'one more thing')).toBe(true)
  })
})

describe('v02test.0029: the composer while the AI answers', () => {
  let locale: typeof i18n.global.locale.value
  beforeEach(() => {
    locale = i18n.global.locale.value
    i18n.global.locale.value = 'ko'
  })
  afterEach(() => {
    i18n.global.locale.value = locale
  })

  function mountComposer(onSend = vi.fn(async () => true)) {
    const wrapper = mount(ConversationComposer, {
      props: {
        chatId: `${A}-composer-${Math.random()}`,
        disabled: false,
        settings: DEFAULT_CHAT_SETTINGS,
        savingSettings: false,
        pendingSourceMode: null,
        providers: [],
        selectedProviderId: '',
        providersStatus: 'ready',
        hasProviders: false,
        sending: false,
        running: true,
        runStarting: false,
        cancelling: false,
        stopState: 'available',
        copyingMention: false,
        manualCopyText: null,
        onSend,
      },
      global: {
        plugins: [i18n],
        stubs: { ComposerModelPicker: true, ComposerSourceModes: true, ComposerSettings: true, ComposerToolsMenu: true },
      },
    })
    return { wrapper, onSend }
  }

  it('an empty input keeps the single stop button', () => {
    const { wrapper } = mountComposer()
    expect(wrapper.get('[data-test="conv-send"]').classes()).toContain('is-stop')
    expect(wrapper.find('[data-test="conv-stop"]').exists()).toBe(false)
  })

  it('typed text turns the button into send and puts the stop beside it; Enter sends', async () => {
    const { wrapper, onSend } = mountComposer()
    const input = wrapper.get('[data-test="conv-input"]')
    expect(input.attributes('disabled')).toBeUndefined()
    await input.setValue('while you answer')
    const send = wrapper.get('[data-test="conv-send"]')
    expect(send.classes()).not.toContain('is-stop')
    expect(send.attributes('type')).toBe('submit')
    expect(send.attributes('aria-label')).toBe('전송')
    const stop = wrapper.get('[data-test="conv-stop"]')
    expect(stop.attributes('aria-label')).toBe('AI 호출 중지')
    await stop.trigger('click')
    expect(wrapper.emitted('stop')).toHaveLength(1)

    await input.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(onSend).toHaveBeenCalledTimes(1)
    expect(onSend.mock.calls[0][0]).toBe('while you answer')
  })
})
