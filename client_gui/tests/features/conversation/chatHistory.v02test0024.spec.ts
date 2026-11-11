import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationRequestError } from '@/features/conversation/api'
import { readLocalChatErrors, redactChatError } from '@/features/conversation/chatErrors'
import { createConversationSession } from '@/features/conversation/conversationSession'
import ConversationTimeline from '@/features/conversation/ConversationTimeline.vue'
import TimelineActivity from '@/features/conversation/TimelineActivity.vue'
import { buildTimeline, replaceServerActivities, type TimelineItem } from '@/features/conversation/timeline'
import type { AiRunState, CommandRequestActivity, StatusActivity, TimelineActivityEntry } from '@/features/conversation/types'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02test.0024 — chat scroll / error history / error copy / run line (0024.0001-B A·B·C·D, decisions 1-4).
registerAppMessages(i18n)

const clipboard = vi.hoisted(() => ({ copyToClipboard: vi.fn(async (_text: string, _host?: HTMLElement | null) => true) }))
vi.mock('@/core/interaction/clipboard', () => clipboard)

const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0004-CH'

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
      { id: B, title: 'Other', updatedAt: '2026-10-05T08:00:00Z' },
    ],
    turns: {
      [A]: [turn(1, 'user', 'look', '2026-10-06T08:02:00Z'), turn(2, 'user', 'run the build', '2026-10-06T08:03:00Z')],
      [B]: [turn(1, 'user', 'other chat', '2026-10-05T08:00:00Z')],
    },
    ...init,
  })
  const session = createConversationSession({ source: fake.source, sleep })
  return { session, ...fake }
}

const runLine = (session: ReturnType<typeof setup>['session']) =>
  session.timeline.value.find((item) => item.kind === 'status' && item.activity.code === 'run_running')
const errorLines = (session: ReturnType<typeof setup>['session']) =>
  session.state.activities.filter((a): a is StatusActivity => a.kind === 'status' && !!a.chatError)

beforeEach(() => {
  localStorage.clear()
  clipboard.copyToClipboard.mockClear()
  i18n.global.locale.value = 'ko'
})

// ── D: one run line with the AI connection name and the read / edit access ─────────────
describe('D: the run in progress line', () => {
  it('shows the AI and the access from the press on, takes the applied access from the server and goes at the end', async () => {
    const clock = manualSleep()
    const { session, source, data } = setup({ settings: { ...createFakeSource().data.settings, sourceAccessMode: 'edit_once' } }, clock.sleep)
    data.runs['run-1'] = ['running', 'running', 'finished']
    data.runDetails['run-1'] = { runId: 'run-1', status: 'running', endReason: null, docRef: A, actionScope: 'chat', providerId: 'sonnet',
      providerName: null, sourceAccess: 'read', startedAt: '2026-10-06T08:04:00Z' }
    await session.setScope(SCOPE)
    await flushPromises()
    session.selectProvider('sonnet')

    const invoking = session.invokeAi()
    // From the press: the pinned AI and the access the stored mode gives (an edit once is an edit).
    expect(session.state.runId).toBe('starting')
    expect(runLine(session)).toMatchObject({ activity: { tone: 'info', createdAt: null, run: { providerName: 'Sonnet 5', access: 'read_write' } } })
    await invoking
    expect(session.state.runId).toBe('run-1')
    expect(session.timeline.value.at(-1)?.key).toBe('status:local-status-run-running')

    // The detail says what the server applied (an edit once that lost its claim runs as read): that is what shows.
    await clock.tick()
    expect(source.fetchRun).toHaveBeenCalledWith('run-1')
    expect(runLine(session)).toMatchObject({ activity: { run: { providerName: 'Sonnet 5', access: 'read' } } })

    data.runDetails['run-1'] = { ...data.runDetails['run-1'], status: 'finished', endReason: 'exited', outcome: 'complete', exitCode: 0,
      finishedAt: '2026-10-06T08:05:00Z' }
    data.turns[A].push(turn(3, 'ai', 'done', '2026-10-06T08:05:00Z', { sourceRunId: 'run-1' }))
    await clock.tick()
    await clock.tick()
    expect(session.state.runId).toBeNull()
    expect(runLine(session)).toBeUndefined()
    expect(session.state.runDisplay).toBeNull()
  })

  it('an adopted run (F5, another tab) shows the line at once and its AI / access from the first detail read', async () => {
    const clock = manualSleep()
    const { session, data } = setup({ activeRun: 'run-9' }, clock.sleep)
    data.runs['run-9'] = ['running']
    data.runDetails['run-9'] = { runId: 'run-9', status: 'running', endReason: null, docRef: A, actionScope: 'chat',
      providerName: 'Claude Opus 5.5 (AG)', sourceAccess: 'read_write' }
    await session.setScope(SCOPE)
    await flushPromises()
    expect(session.state.runId).toBe('run-9')
    expect(runLine(session)).toMatchObject({ activity: { run: { providerName: null, access: null } } })
    await clock.tick()
    expect(runLine(session)).toMatchObject({ activity: { run: { providerName: 'Claude Opus 5.5 (AG)', access: 'read_write' } } })
  })

  it('a call refused before any run leaves no run line', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(httpError(422, 'bad request'))
    await session.invokeAi()
    expect(session.state.runId).toBeNull()
    expect(runLine(session)).toBeUndefined()
  })

  it('the line reads AI · name · access, each once it is known', async () => {
    const line = (run: StatusActivity['run']): StatusActivity => ({ kind: 'status', id: 'x', createdAt: null, tone: 'info', code: 'run_running', text: null, run })
    const mounted = mount(TimelineActivity, { props: { activity: line({ providerName: 'Claude Opus 5.5 (AG)', access: 'read_write' }) }, global: { plugins: [i18n] } })
    expect(mounted.get('[data-test="tl-status"]').text()).toBe('AI 호출 중 · Claude Opus 5.5 (AG) · 편집')
    await mounted.setProps({ activity: line({ providerName: null, access: 'read' }) })
    expect(mounted.get('[data-test="tl-status"]').text()).toBe('AI 호출 중 · 읽기')
    // Not an error: nothing to copy.
    expect(mounted.find('[data-test="tl-status-copy"]').exists()).toBe(false)
    mounted.unmount()
  })
})

// ── B: errors in the history, at their time, kept across F5 / reopening ─────────────────
describe('B: chat errors are part of the history', () => {
  it('a refused AI call is a line at its time, recorded on the server and read back on reopening', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.startChatRun.mockRejectedValueOnce(httpError(503, 'AI provider unavailable'))
    await session.invokeAi()
    await flushPromises()
    expect(session.state.notice).toBeNull()
    expect(errorLines(session)).toMatchObject([{ code: 'invoke_failed', tone: 'danger', text: 'AI provider unavailable' }])
    expect(errorLines(session)[0].createdAt).toEqual(expect.any(String))
    expect(source.recordChatError).toHaveBeenCalledTimes(1)
    expect(readLocalChatErrors('guest', A)).toEqual([])

    // F5 / another chat and back: the line comes back from the server's activity read, once.
    await session.selectChat(B)
    await flushPromises()
    expect(errorLines(session)).toEqual([])
    await session.selectChat(A)
    await flushPromises()
    expect(errorLines(session)).toMatchObject([{ code: 'invoke_failed', text: 'AI provider unavailable' }])
    // Placed by its time: after the earlier turns, a message sent later goes after it.
    const keys = session.timeline.value.map((item) => item.key)
    expect(keys.indexOf(`status:${errorLines(session)[0].id}`)).toBeGreaterThan(keys.indexOf('turn:2'))
  })

  it('without an answer the error stays in this browser (no secret) and comes back from there', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.postTurn.mockRejectedValue(new ConversationRequestError('network down Authorization: Bearer abc.def', null, null))
    expect(await session.send('hello')).toBe(false)
    await flushPromises()
    expect(source.recordChatError).not.toHaveBeenCalled()
    const kept = readLocalChatErrors('guest', A)
    expect(kept).toMatchObject([{ code: 'send_failed' }])
    expect(kept[0].message).not.toContain('abc.def')
    expect(JSON.stringify(localStorage)).not.toContain('abc.def')
    await session.selectChat(B)
    await session.selectChat(A)
    await flushPromises()
    expect(errorLines(session)).toMatchObject([{ code: 'send_failed', id: `chat-error:${kept[0].id}` }])
  })

  it('a record the server could not take is kept in this browser instead', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.recordChatError.mockRejectedValueOnce(httpError(500, 'store down'))
    source.startChatRun.mockRejectedValueOnce(httpError(422, 'refused'))
    await session.invokeAi()
    await flushPromises()
    expect(readLocalChatErrors('guest', A)).toMatchObject([{ code: 'invoke_failed', message: 'refused' }])
  })

  it('the server copy of an error replaces the one placed here (never twice)', () => {
    const local: StatusActivity = { kind: 'status', id: 'chat-error:err-1', createdAt: '2026-10-06T08:04:00Z', tone: 'danger', code: 'send_failed', text: 'x', chatError: true }
    const run: StatusActivity = { kind: 'status', id: 'local-status-run:a:r:result', createdAt: null, tone: 'warning', code: 'run_no_reply', text: null }
    const merged = replaceServerActivities([local, run], [{ ...local, text: 'server words' }])
    expect(merged.filter((a) => a.id === local.id)).toEqual([{ ...local, text: 'server words' }])
    expect(merged).toContainEqual(run)
  })

  it('other notices stay notices (settings, chat creation)', async () => {
    const { session, source } = setup()
    await session.setScope(SCOPE)
    await flushPromises()
    source.saveChatSettings.mockRejectedValueOnce(httpError(500, 'nope'))
    await session.saveSettings({ send_action: 'none' })
    expect(session.state.notice).toMatchObject({ code: 'settings_failed' })
    expect(errorLines(session)).toEqual([])
  })

  it('a result line without a finish time is placed by its start, not pinned under every later turn', async () => {
    const run: AiRunState = { runId: 'r1', status: 'finished', endReason: 'all_providers_failed', docRef: A, actionScope: 'chat',
      finishedAt: null, startedAt: '2026-10-06T08:02:30Z', failure: { cause: null, message: null, workdir: null, detail: 'remote_agent_no_response: not_claimed' } }
    const { session, data } = setup({ runDetails: { r1: run } })
    data.activities[A] = [{ kind: 'command', id: 'k1', runId: 'r1', createdAt: null, command: 'x', state: 'cancelled',
      anchor: { seq: 1, position: 'after', state: 'run_start' } } as CommandRequestActivity]
    await session.setScope(SCOPE)
    await flushPromises()
    await flushPromises()
    const keys = session.timeline.value.map((item) => item.key)
    const at = keys.indexOf('status:local-status-run:p.m.0001.0002-CH:r1:result')
    expect(at).toBeGreaterThan(-1)
    // turn:2 (08:03) was said after the run started (08:02:30): the line sits above it, not at the bottom.
    expect(at).toBeLessThan(keys.indexOf('turn:2'))
  })

  it('redacts credential-looking words', () => {
    expect(redactChatError('401 Bearer abc.DEF-1 token=s3cr3t ok')).toBe('401 Bearer *** token=*** ok')
  })
})

// ── C: copy of an error line and of a failed command ───────────────────────────────────
describe('C: copy', () => {
  let wrappers: VueWrapper[] = []
  afterEach(() => {
    for (const w of wrappers) w.unmount()
    wrappers = []
  })
  function mountActivity(activity: TimelineActivityEntry) {
    const w = mount(TimelineActivity, { props: { activity }, attachTo: document.body, global: { plugins: [i18n] } })
    wrappers.push(w)
    return w
  }

  it('an error line copies its whole text with the message copy feedback', async () => {
    const detail = 'remote_agent_no_response: not_claimed\nsecond line /very/long/path/of/the/job'
    const w = mountActivity({ kind: 'status', id: 's1', createdAt: '2026-10-06T08:04:00Z', tone: 'danger', code: 'run_no_reply', text: detail })
    const button = w.get('[data-test="tl-status-copy"]')
    expect(button.attributes('aria-label')).toBe('원문 복사')
    await button.trigger('click')
    await flushPromises()
    expect(clipboard.copyToClipboard).toHaveBeenCalledWith(`AI 호출이 답변 없이 끝났습니다 (${detail})`, expect.anything())
    expect(w.get('[data-test="tl-status-copy"]').attributes('data-state')).toBe('done')
  })

  it('a recorded chat error keeps its notice wording and copies it', async () => {
    const w = mountActivity({ kind: 'status', id: 'chat-error:e', createdAt: null, tone: 'danger', code: 'send_failed', text: 'corrupted body', chatError: true })
    expect(w.get('[data-test="tl-status"]').text()).toContain('메시지를 보내지 못했습니다 (corrupted body)')
    await w.get('[data-test="tl-status-copy"]').trigger('click')
    await flushPromises()
    expect(clipboard.copyToClipboard).toHaveBeenCalledWith('메시지를 보내지 못했습니다 (corrupted body)', expect.anything())
  })

  it('a failed copy says so', async () => {
    clipboard.copyToClipboard.mockResolvedValueOnce(false)
    const w = mountActivity({ kind: 'status', id: 's2', createdAt: null, tone: 'warning', code: 'run_result_unconfirmed', text: null })
    await w.get('[data-test="tl-status-copy"]').trigger('click')
    await flushPromises()
    expect(w.get('[data-test="tl-status-copy"]').attributes('data-state')).toBe('failed')
  })

  it('a failed command copies the command, its path, the reason and its output; a succeeded one has no copy', async () => {
    const failed: CommandRequestActivity = { kind: 'command', id: 'c1', createdAt: null, command: 'pytest -q server/tests', cwd: 'server',
      state: 'failed', exitCode: 2, errorCode: null, startedAt: '2026-10-06T08:04:00Z', stderr: 'E   AssertionError: boom', stdout: '1 failed' }
    const w = mountActivity(failed)
    await w.get('[data-test="tl-command-copy"]').trigger('click')
    await flushPromises()
    const text = clipboard.copyToClipboard.mock.calls[0][0]
    expect(text).toContain('pytest -q server/tests')
    expect(text).toContain('server')
    expect(text).toContain('2')
    expect(text).toContain('E   AssertionError: boom')
    expect(text).toContain('1 failed')
    const ok = mountActivity({ ...failed, id: 'c2', state: 'succeeded', exitCode: 0, stderr: '' })
    expect(ok.find('[data-test="tl-command-copy"]').exists()).toBe(false)
  })

  it('every error line has its own copy', () => {
    const items: TimelineItem[] = buildTimeline([], [
      { kind: 'status', id: 'e1', createdAt: '2026-10-06T08:00:00Z', tone: 'danger', code: 'send_failed', text: 'one', chatError: true },
      { kind: 'status', id: 'e2', createdAt: '2026-10-06T08:01:00Z', tone: 'danger', code: 'invoke_failed', text: 'two', chatError: true },
    ], [])
    const w = mount(ConversationTimeline, { props: { items, status: 'ready', hasMoreBefore: false, loadingOlder: false, deciding: [], emptyText: '', chatId: 'c' },
      attachTo: document.body, global: { plugins: [i18n] } })
    wrappers.push(w)
    expect(w.findAll('[data-test="tl-status-copy"]')).toHaveLength(2)
  })
})

// ── A: scrolling back keeps the reader's place; in-place changes follow the tail ───────
describe('A: scroll', () => {
  let wrapper: VueWrapper | null = null
  // The log re-pins one and two frames after a render; frames run at once here so no late re-pin lands after a step.
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { cb(0); return 0 })
  })
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    vi.unstubAllGlobals()
  })
  const msg = (seq: number): TimelineItem => ({ kind: 'message', key: `turn:${seq}`, turn: turn(seq, 'user', `m${seq}`, null), pending: false, failed: false, localId: null })

  async function mountLog(items: TimelineItem[], hasMoreBefore = true) {
    wrapper = mount(ConversationTimeline, {
      props: { items, status: 'ready', hasMoreBefore, loadingOlder: false, deciding: [], emptyText: '', chatId: 'c' },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    const el = wrapper.get('[data-test="conv-log"]').element as HTMLElement
    let top = 0
    let height = 300
    Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 200 })
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => height })
    Object.defineProperty(el, 'scrollTop', { configurable: true, get: () => top, set: (v: number) => { top = v } })
    await flushPromises()
    return { el, grow: (h: number) => { height = h }, at: () => top, set: (v: number) => { top = v } }
  }

  it('loading older turns from a short log keeps the reader where they were (no jump to the bottom)', async () => {
    const log = await mountLog([msg(31), msg(32)])
    // A short log: the reader at the top is also "at the bottom" (pinned) — the case the bottom pin broke.
    log.set(0)
    await wrapper!.get('[data-test="conv-log"]').trigger('scroll')
    // The page request goes out; while it is in flight the older turns arrive in front.
    await wrapper!.setProps({ loadingOlder: true })
    log.grow(900)
    await wrapper!.setProps({ items: [msg(29), msg(30), msg(31), msg(32)] })
    await wrapper!.setProps({ loadingOlder: false })
    await flushPromises()
    // Back where the reader was: the prepended 600px are added to the old position, not the bottom (900).
    expect(log.at()).toBe(600)
    expect(wrapper!.emitted('load-older')).toHaveLength(1)
  })

  it('a card changing in place follows the tail for a reader at the bottom, never for one scrolled up', async () => {
    const command = (state: CommandRequestActivity['state']): TimelineItem => ({ kind: 'command', key: 'command:k1',
      activity: { kind: 'command', id: 'k1', createdAt: null, command: 'git status', state } })
    const log = await mountLog([msg(1), command('pending_approval')], false)
    log.set(100) // at the bottom (300 - 100 - 200 = 0)
    await wrapper!.get('[data-test="conv-log"]').trigger('scroll')
    log.grow(500)
    await wrapper!.setProps({ items: [msg(1), command('running')] })
    await flushPromises()
    expect(log.at()).toBe(500)

    log.set(10) // scrolled up
    await wrapper!.get('[data-test="conv-log"]').trigger('scroll')
    log.grow(700)
    await wrapper!.setProps({ items: [msg(1), command('succeeded')] })
    await flushPromises()
    expect(log.at()).toBe(10)
  })
})
