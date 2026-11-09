import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia } from 'pinia'
import { defineComponent, h } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import { CHAT_EVENT_COALESCE_MS, CHAT_REFRESH_EVENTS, chatEventRefresh, type ChatEventFrame, type ChatEventSubscription } from '@/features/conversation/conversationEvents'
import { REVISIT_CATCHUP_MIN_MS, RUN_POLL_MS, type ConversationSession } from '@/features/conversation/conversationSession'
import DesignerRightDock from '@/features/designer/shell/DesignerRightDock.vue'
import { createFakeSource, turn } from './fakeConversationSource'

// flowgate.v02test.0013 — the chat panel hears the server: every event about the open chat (a stored message, its
// AI run starting / ending, a command request, a run's changes) and a stream reconnect recover the chat through
// the session; showing the Dock's chat tab again is a return like the browser tab's. The project stream is handed
// in by the container (`subscribeEvents`); here a fake hub stands in for it.
registerAppMessages(i18n)

const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0003-CH'
const GROUP = 'p.m.0001'
let wrapper: VueWrapper | null = null
let now = 1_000_000

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  now = 1_000_000
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
  vi.spyOn(Date, 'now').mockImplementation(() => now)
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  delete (document as unknown as Record<string, unknown>).visibilityState
  vi.restoreAllMocks()
  vi.useRealTimers()
})

/** The project hub as the panel sees it: subscriptions by project, frames delivered by hand. */
function fakeHub() {
  const subs: Array<{ projectId: string; sub: ChatEventSubscription; active: boolean }> = []
  const subscribe = vi.fn((projectId: string, sub: ChatEventSubscription) => {
    const entry = { projectId, sub, active: true }
    subs.push(entry)
    return () => { entry.active = false }
  })
  const live = () => subs.filter((s) => s.active)
  return {
    subscribe,
    live,
    emit(frame: Partial<ChatEventFrame> & { type: string }) {
      const full: ChatEventFrame = { payload: {}, groupId: null, docId: null, ...frame }
      for (const s of live()) if (s.sub.events.includes(full.type)) s.sub.event(full)
    },
    resync() {
      for (const s of live()) s.sub.resync?.()
    },
  }
}

async function settle(ms = CHAT_EVENT_COALESCE_MS) {
  await vi.advanceTimersByTimeAsync(ms)
  await flushPromises()
}

async function mountPanel(props: Record<string, unknown> = {}) {
  const hub = fakeHub()
  const fake = createFakeSource({
    chats: [{ id: A, title: 'A', updatedAt: null }, { id: B, title: 'B', updatedAt: null }],
    turns: { [A]: [turn(1, 'user', 'fix it')], [B]: [turn(1, 'user', 'other')] },
  })
  fake.source.fetchRun.mockImplementation(async (id: string) => ({ runId: id, status: 'running', endReason: null, outcome: null,
    exitCode: null, succeeded: null, docRef: A, actionScope: 'chat', groupId: GROUP }))
  wrapper = mount(ConversationPanel, {
    props: { projectId: 'p', groupId: GROUP, source: fake.source, subscribeEvents: hub.subscribe, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  await flushPromises()
  const session = (wrapper.vm as unknown as { session: ConversationSession }).session
  return { ...fake, session, hub }
}

const bodies = (s: ConversationSession) =>
  s.timeline.value.filter((item) => item.kind === 'message').map((item) => (item.kind === 'message' ? item.turn.body : ''))

describe('v02test.0013 chatEventRefresh: which frames are about the open chat', () => {
  const target = { projectId: 'p', groupId: GROUP, chatId: A }
  it('matches the CH by doc_id, or by payload.doc_id for command / run-change frames', () => {
    expect(chatEventRefresh({ type: 'conversation_turn_appended', payload: {}, groupId: GROUP, docId: A }, target)).toEqual({ turns: true })
    expect(chatEventRefresh({ type: 'ai_invoke_started', payload: {}, groupId: GROUP, docId: A }, target)).toEqual({ run: true })
    expect(chatEventRefresh({ type: 'chat_command_updated', payload: { doc_id: A }, groupId: GROUP, docId: null }, target)).toEqual({ activities: true })
    expect(chatEventRefresh({ type: 'chat_run_changes', payload: { doc_id: A, group_id: GROUP }, groupId: GROUP, docId: null }, target)).toEqual({ activities: true })
    expect(chatEventRefresh({ type: 'ai_invoke_finished', payload: {}, groupId: GROUP, docId: A }, target)).toEqual({ turns: true, activities: true, history: true })
  })
  it('ignores another chat, another Flow, a frame without CH and an event it does not listen to', () => {
    expect(chatEventRefresh({ type: 'conversation_turn_appended', payload: {}, groupId: GROUP, docId: B }, target)).toBeNull()
    expect(chatEventRefresh({ type: 'conversation_turn_appended', payload: {}, groupId: 'p.m.0002', docId: A }, target)).toBeNull()
    expect(chatEventRefresh({ type: 'chat_command_updated', payload: {}, groupId: GROUP, docId: null }, target)).toBeNull()
    expect(chatEventRefresh({ type: 'group_view_refresh', payload: {}, groupId: GROUP, docId: A }, target)).toBeNull()
    expect(chatEventRefresh({ type: 'toString', payload: {}, groupId: GROUP, docId: A }, target)).toBeNull()
  })
})

describe('v02test.0013 the panel follows the server events of the open chat', () => {
  it('subscribes once for the project and shows a message stored elsewhere', async () => {
    const { hub, data, session, source } = await mountPanel()
    expect(hub.subscribe).toHaveBeenCalledTimes(1)
    expect(hub.subscribe.mock.calls[0][0]).toBe('p')
    expect([...hub.subscribe.mock.calls[0][1].events].sort()).toEqual([...CHAT_REFRESH_EVENTS].sort())

    data.turns[A].push(turn(2, 'user', 'from another window'))
    hub.emit({ type: 'conversation_turn_appended', groupId: GROUP, docId: A })
    expect(bodies(session)).toEqual(['fix it']) // coalescing window
    await settle()
    expect(bodies(session)).toEqual(['fix it', 'from another window'])

    // Another chat's or Flow's frame reads nothing.
    const reads = source.fetchTurns.mock.calls.length
    hub.emit({ type: 'conversation_turn_appended', groupId: GROUP, docId: B })
    hub.emit({ type: 'conversation_turn_appended', groupId: 'p.m.0002', docId: A })
    await settle()
    expect(source.fetchTurns.mock.calls.length).toBe(reads)
  })

  it('frames within the coalescing window are one recovery', async () => {
    const { hub, source } = await mountPanel()
    const turns = source.fetchTurns.mock.calls.length
    const activities = source.listActivities.mock.calls.length
    hub.emit({ type: 'conversation_turn_appended', groupId: GROUP, docId: A })
    hub.emit({ type: 'chat_command_updated', groupId: GROUP, payload: { doc_id: A } })
    hub.emit({ type: 'chat_run_changes', groupId: GROUP, payload: { doc_id: A } })
    await settle()
    expect(source.fetchTurns.mock.calls.length).toBe(turns + 1)
    expect(source.listActivities.mock.calls.length).toBe(activities + 1)
  })

  it('a run started elsewhere (ai_invoke_started) is adopted and polled', async () => {
    const { hub, data, session } = await mountPanel()
    expect(session.running.value).toBe(false)
    data.activeRun = 'r9'
    hub.emit({ type: 'ai_invoke_started', groupId: GROUP, docId: A, payload: { run_id: 'r9', doc_ref: A } })
    await settle()
    expect(session.state.runId).toBe('r9')
  })

  it('a reconnect of the stream recovers everything', async () => {
    const { hub, source } = await mountPanel()
    const active = source.fetchActiveChatRun.mock.calls.length
    const activities = source.listActivities.mock.calls.length
    const history = source.listRuns.mock.calls.length
    hub.resync()
    await settle()
    expect(source.fetchActiveChatRun.mock.calls.length).toBe(active + 1)
    expect(source.listActivities.mock.calls.length).toBe(activities + 1)
    expect(source.listRuns.mock.calls.length).toBe(history + 1)
  })

  it('a pending signal is dropped with the chat it was for', async () => {
    const { hub, session, source } = await mountPanel()
    hub.emit({ type: 'chat_command_updated', groupId: GROUP, payload: { doc_id: A } })
    await session.selectChat(B)
    await flushPromises()
    const activities = source.listActivities.mock.calls.filter((call: unknown[]) => call[0] === A).length
    await settle()
    expect(source.listActivities.mock.calls.filter((call: unknown[]) => call[0] === A).length).toBe(activities)
  })

  it('unsubscribes when the panel goes away', async () => {
    const { hub } = await mountPanel()
    expect(hub.live()).toHaveLength(1)
    wrapper!.unmount()
    wrapper = null
    expect(hub.live()).toHaveLength(0)
  })

  it('without a stream (no subscribeEvents) the panel still works and hears nothing', async () => {
    const { session } = await mountPanel({ subscribeEvents: undefined })
    expect(session.state.turnsStatus).toBe('ready')
  })
})

describe('v02test.0013 coming back recovers, a polled run included', () => {
  it('showing the Dock chat tab again recovers; a window focus while another tab is shown does not', async () => {
    const { source } = await mountPanel({ active: false })
    const activities = source.listActivities.mock.calls.length
    window.dispatchEvent(new Event('focus'))
    await flushPromises()
    expect(source.listActivities.mock.calls.length).toBe(activities)

    await wrapper!.setProps({ active: true })
    await flushPromises()
    expect(source.listActivities.mock.calls.length).toBe(activities + 1)

    // Within REVISIT_CATCHUP_MIN_MS a second return reads nothing more.
    await wrapper!.setProps({ active: false })
    await wrapper!.setProps({ active: true })
    await flushPromises()
    expect(source.listActivities.mock.calls.length).toBe(activities + 1)
    now += REVISIT_CATCHUP_MIN_MS
    await wrapper!.setProps({ active: false })
    await wrapper!.setProps({ active: true })
    await flushPromises()
    expect(source.listActivities.mock.calls.length).toBe(activities + 2)
  })

  it('a return while a run is polled wakes its poll now instead of waiting RUN_POLL_MS', async () => {
    const { source, session } = await mountPanel()
    source.startChatRun.mockResolvedValueOnce('r1')
    await session.invokeAi()
    await flushPromises()
    expect(session.state.runId).toBe('r1')
    const ticks = source.fetchRun.mock.calls.length
    window.dispatchEvent(new Event('focus'))
    await flushPromises()
    expect(source.fetchRun.mock.calls.length).toBe(ticks + 1)
    await vi.advanceTimersByTimeAsync(RUN_POLL_MS)
    await flushPromises()
    expect(source.fetchRun.mock.calls.length).toBe(ticks + 2)
  })
})

describe('v02test.0013 the right Dock hands the chat its stream and whether it is shown', () => {
  const ConversationStub = defineComponent({
    name: 'ConversationPanel',
    props: { projectId: { type: String, default: null }, groupId: { type: String, default: null },
      active: { type: Boolean, default: true }, subscribeEvents: { type: Function, default: undefined } },
    setup: (props) => () => h('section', { 'data-test': 'conversation-panel', 'data-active': String(props.active),
      'data-stream': props.subscribeEvents ? 'yes' : 'no' }),
  })
  const GitStub = defineComponent({ name: 'DesignerGitPanel', setup: () => () => h('section') })

  function mountDock(withPinia: boolean, tool: 'conversation' | 'git') {
    return mount(DesignerRightDock, {
      props: { projectId: 'p', activeFlow: { groupId: GROUP, name: 'F' }, tool, collapsed: false },
      global: { plugins: withPinia ? [i18n, createPinia()] : [i18n], stubs: { ConversationPanel: ConversationStub, DesignerGitPanel: GitStub } },
    })
  }

  it('active only while the chat tab is the one shown; the stream comes from the app store', async () => {
    const dock = mountDock(true, 'conversation')
    await flushPromises()
    const panel = () => dock.get('[data-test="conversation-panel"]')
    expect(panel().attributes('data-active')).toBe('true')
    expect(panel().attributes('data-stream')).toBe('yes')
    await dock.setProps({ tool: 'git' })
    expect(panel().attributes('data-active')).toBe('false')
    await dock.setProps({ tool: 'conversation', collapsed: true })
    expect(panel().attributes('data-active')).toBe('false')
    dock.unmount()
  })

  it('without the app Pinia it hands in no stream', async () => {
    const dock = mountDock(false, 'conversation')
    await flushPromises()
    expect(dock.get('[data-test="conversation-panel"]').attributes('data-stream')).toBe('no')
    dock.unmount()
  })
})
