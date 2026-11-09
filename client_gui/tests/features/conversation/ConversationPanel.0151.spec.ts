import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import ConversationTimeline from '@/features/conversation/ConversationTimeline.vue'
import { REVISIT_CATCHUP_MIN_MS, type ConversationSession } from '@/features/conversation/conversationSession'
import type { WorkChangeActivity } from '@/features/conversation/types'
import { createFakeSource, turn } from './fakeConversationSource'

// flowgate.v02.0151 T-A2 (NR Rev.4 §2.6-3 ②, §2.8 V8): coming back to the panel — the tab visible again, the window
// focused again — catches the open chat up, activities included; at most once per REVISIT_CATCHUP_MIN_MS, never while
// hidden. The panel's existing reload path (same chat) brings activities too. flowgate.v02test.0013: a polled run's
// poll is woken instead of being skipped.
registerAppMessages(i18n)

const A = 'p.m.0001.0002-CH'
let wrapper: VueWrapper | null = null
let visibility: DocumentVisibilityState = 'visible'
let now = 1_000_000

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  visibility = 'visible'
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
  now = 1_000_000
  vi.spyOn(Date, 'now').mockImplementation(() => now)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  delete (document as unknown as Record<string, unknown>).visibilityState
  vi.restoreAllMocks()
  vi.useRealTimers()
})

function lateChange(): WorkChangeActivity {
  return { kind: 'change', id: 'r0', runId: 'r0', createdAt: null, anchor: { seq: 2, position: 'after', state: 'reply' },
    files: [{ path: 'src/late.ts', status: 'M', insertions: 3, deletions: 1 }], filesChanged: 1, insertions: 3, deletions: 1 }
}

async function mountPanel() {
  const fake = createFakeSource({
    chats: [{ id: A, title: 'A', updatedAt: null }],
    turns: { [A]: [turn(1, 'user', 'fix it', '2026-10-06T08:00:00Z'), turn(2, 'ai', 'fixed', '2026-10-06T08:01:00Z', { sourceRunId: 'r0' })] },
  })
  fake.source.fetchRun.mockImplementation(async (id: string) => ({ runId: id, status: 'finished', endReason: 'exited', outcome: 'complete',
    exitCode: 0, succeeded: true, docRef: A, actionScope: 'chat', groupId: 'p.m.0001' }))
  wrapper = mount(ConversationPanel, {
    props: { projectId: 'p', groupId: 'p.m.0001', source: fake.source },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  await flushPromises()
  const session = (wrapper.vm as unknown as { session: ConversationSession }).session
  return { ...fake, session }
}

const changeCards = () => wrapper!.findAll('[data-test="tl-change"]')
const visibilityChange = async () => { document.dispatchEvent(new Event('visibilitychange')); await flushPromises() }
const windowFocus = async () => { window.dispatchEvent(new Event('focus')); await flushPromises() }

describe('0151 T-A2 panel revisit catch-up (V8)', () => {
  it('becoming visible catches the open chat up once and shows a late run change; hidden does nothing', async () => {
    const { source, data, session } = await mountPanel()
    expect(session.state.turnsStatus).toBe('ready')
    expect(changeCards()).toHaveLength(0)
    data.activities[A] = [lateChange()]
    const reads = source.listActivities.mock.calls.length

    visibility = 'hidden'
    await visibilityChange()
    await windowFocus()
    expect(source.listActivities.mock.calls.length).toBe(reads)

    visibility = 'visible'
    await visibilityChange()
    expect(source.listActivities.mock.calls.length).toBe(reads + 1)
    expect(changeCards()).toHaveLength(1)
    expect(session.timeline.value.filter((item) => item.kind !== 'status').map((item) => item.key)).toEqual(['turn:1', 'turn:2', 'change:r0'])

    // The focus of the same return falls inside the gap: no second read.
    now += 500
    await windowFocus()
    expect(source.listActivities.mock.calls.length).toBe(reads + 1)
    now += REVISIT_CATCHUP_MIN_MS
    await windowFocus()
    expect(source.listActivities.mock.calls.length).toBe(reads + 2)
    expect(changeCards()).toHaveLength(1)
  })

  // flowgate.v02test.0013: a return while a run is polled is no longer skipped — the poll may be behind (a throttled
  // background tab, missed events). It wakes the poll instead of reading beside it: one tick now, no history read.
  it('while an AI run is polled a return wakes its poll instead of a catch-up beside it', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const { source, session } = await mountPanel()
    source.startChatRun.mockResolvedValueOnce('r1')
    source.fetchRun.mockImplementation(async (id: string) => ({ runId: id, status: 'running', endReason: null, outcome: null,
      exitCode: null, succeeded: null, docRef: A, actionScope: 'chat', groupId: 'p.m.0001' }))
    await session.invokeAi()
    await flushPromises()
    expect(session.running.value).toBe(true)
    const ticks = source.fetchRun.mock.calls.length
    const history = source.listRuns.mock.calls.length
    await visibilityChange()
    await windowFocus()
    expect(source.fetchRun.mock.calls.length).toBe(ticks + 1) // one tick; the focus of the same return adds none
    expect(source.listRuns.mock.calls.length).toBe(history)
    expect(session.state.runId).toBe('r1')
  })

  it('removes the listeners it added when the panel goes away', async () => {
    const docAdd = vi.spyOn(document, 'addEventListener')
    const docRemove = vi.spyOn(document, 'removeEventListener')
    const winAdd = vi.spyOn(window, 'addEventListener')
    const winRemove = vi.spyOn(window, 'removeEventListener')
    await mountPanel()
    const onVisibility = docAdd.mock.calls.find((call) => call[0] === 'visibilitychange')?.[1]
    const onFocus = winAdd.mock.calls.find((call) => call[0] === 'focus')?.[1]
    expect(onVisibility).toBeTypeOf('function')
    expect(onFocus).toBeTypeOf('function')
    wrapper!.unmount()
    wrapper = null
    expect(docRemove).toHaveBeenCalledWith('visibilitychange', onVisibility)
    expect(winRemove).toHaveBeenCalledWith('focus', onFocus)
  })

  it('the timeline reload path (reloadChat -> same chat) also re-reads the activities', async () => {
    const { source, data } = await mountPanel()
    data.activities[A] = [lateChange()]
    const reads = source.listActivities.mock.calls.length
    wrapper!.findComponent(ConversationTimeline).vm.$emit('reload')
    await flushPromises()
    expect(source.listActivities.mock.calls.length).toBe(reads + 1)
    expect(changeCards()).toHaveLength(1)
  })
})
