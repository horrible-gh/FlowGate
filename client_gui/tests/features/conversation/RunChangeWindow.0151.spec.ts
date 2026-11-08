import { DOMWrapper, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import RunChangeWindow from '@/features/conversation/RunChangeWindow.vue'
import type { ConversationSession } from '@/features/conversation/conversationSession'
import type { RunFileDiff, WorkChangeActivity } from '@/features/conversation/types'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// NR Rev.4 §3: the real panel + ModelessShell + stored-diff renderer, using the same
// fakeConversationSource and sourceRunId/anchor fixtures as A's V12 attribution tests.
registerAppMessages(i18n)
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0004-CH'
const pathA = 'src/a.ts'
const pathB = 'src/b.ts'
let wrapper: VueWrapper | null = null

function change(runId = 'R1', paths = [pathA, pathB], seq = 6): WorkChangeActivity {
  return { kind: 'change', id: runId, runId, createdAt: null,
    files: paths.map((path) => ({ path, status: 'M', insertions: 1, deletions: 1 })),
    filesChanged: paths.length, insertions: paths.length, deletions: paths.length,
    anchor: { seq, position: 'after', state: 'reply' } }
}
function diff(runId = 'R1', path = pathA, text = 'new\n'): RunFileDiff {
  return { runId, path, status: 'M',
    old: { exists: true, binary: false, truncated: false, size: 4, content: 'old\n' },
    new: { exists: true, binary: false, truncated: false, size: text.length, content: text } }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
const body = () => new DOMWrapper(document.body)
const windows = () => body().findAll('[data-test="rc-window"]')
const viewer = (runId = 'R1') => body().find(`[data-test="rc-window"][data-run-id="${runId}"]`)
const shell = (runId = 'R1') => new DOMWrapper(viewer(runId).element.closest('.ui-modeless-panel') as HTMLElement)
const row = (path: string, runId = 'R1') => viewer(runId).find(`[data-test="rc-window-file"][data-path="${path}"]`)
const panelFile = (path: string) => wrapper!.find(`[data-test="tl-change-file"][data-path="${path}"]`)
const session = () => (wrapper!.vm as unknown as { session: ConversationSession }).session

async function open(init: Parameters<typeof createFakeSource>[0] = {}) {
  const fake = createFakeSource({
    chats: [{ id: A, title: 'A', updatedAt: null }, { id: B, title: 'B', updatedAt: null }],
    turns: { [A]: [turn(5, 'user', 'fix'), turn(6, 'ai', 'fixed', null, { sourceRunId: 'R1' })], [B]: [] },
    activities: { [A]: [change()], [B]: [] },
    diffs: { [`R1\n${pathA}`]: diff(), [`R1\n${pathB}`]: diff('R1', pathB, 'second\n') },
    ...init,
  })
  await mountPanel(fake)
  return fake
}
async function mountPanel(fake: ReturnType<typeof createFakeSource>) {
  wrapper = mount(ConversationPanel, { props: { projectId: 'p', groupId: 'p.m.0001', source: fake.source },
    attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises()
}
async function openCard(index = 0) {
  await wrapper!.findAll('[data-test="tl-change-open"]')[index].trigger('click')
  await flushPromises()
}
function noInlineDiff() {
  expect(wrapper!.find('[data-test="conv-log"]').find('[data-test="rc-diff"]').exists()).toBe(false)
}
beforeEach(() => { i18n.global.locale.value = 'ko'; localStorage.clear() })
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('0151 T-B modeless run changes', () => {
  it('opens without a parent listener, selects a clicked file, caches each file and keeps code out of the chat', async () => {
    const { source } = await open()
    expect(source.fetchRunFileDiff).not.toHaveBeenCalled()
    await openCard()
    expect(windows()).toHaveLength(1)
    expect(row(pathA).attributes('aria-pressed')).toBe('true')
    expect(viewer().find('[data-test="rc-diff-lines"]').text()).toContain('+new')
    await panelFile(pathB).trigger('click')
    await flushPromises()
    expect(windows()).toHaveLength(1)
    expect(row(pathB).attributes('aria-pressed')).toBe('true')
    expect(viewer().text()).toContain('+second')
    await row(pathA).trigger('click')
    await flushPromises()
    expect(source.fetchRunFileDiff.mock.calls).toEqual([[A, 'R1', pathA], [A, 'R1', pathB]])
    expect(source.decideCommand).not.toHaveBeenCalled()
    noInlineDiff()
  })

  it('shows loading, deduplicates a repeated selection and keeps out-of-order file reads in their own slots', async () => {
    const { source } = await open()
    const first = deferred<RunFileDiff>()
    const second = deferred<RunFileDiff>()
    source.fetchRunFileDiff.mockImplementation((_chat: string, _run: string, path: string) => path === pathA ? first.promise : second.promise)
    await panelFile(pathA).trigger('click')
    expect(viewer().find('[data-test="rc-diff-loading"]').exists()).toBe(true)
    await panelFile(pathA).trigger('click')
    expect(source.fetchRunFileDiff).toHaveBeenCalledTimes(1)
    await row(pathB).trigger('click')
    second.resolve(diff('R1', pathB, 'B wins\n'))
    await flushPromises()
    first.resolve(diff('R1', pathA, 'A late\n'))
    await flushPromises()
    expect(viewer().text()).toContain('+B wins')
    expect(viewer().text()).not.toContain('A late')
    await row(pathA).trigger('click')
    expect(viewer().text()).toContain('+A late')
    expect(source.fetchRunFileDiff).toHaveBeenCalledTimes(2)
    noInlineDiff()
  })

  it.each([404, 409])('shows a %s read failure in the window and retries without a chat notice', async (status) => {
    const { source } = await open()
    source.fetchRunFileDiff.mockRejectedValueOnce(httpError(status, 'not available'))
    await openCard()
    expect(viewer().find('[data-test="rc-diff-error"]').exists()).toBe(true)
    expect(wrapper!.find('[data-test="conv-notice"]').exists()).toBe(false)
    await viewer().find('[data-test="rc-window-retry"]').trigger('click')
    await flushPromises()
    expect(viewer().find('[data-test="rc-diff-lines"]').exists()).toBe(true)
    expect(source.fetchRunFileDiff).toHaveBeenCalledTimes(2)
    noInlineDiff()
  })

  it.each(['empty', 'unchanged', 'binary', 'too_large', 'long'] as const)('renders the %s diff state', async (kind) => {
    const answer = diff()
    if (kind === 'empty') { answer.old.content = ''; answer.new.content = '' }
    if (kind === 'unchanged') answer.new.content = answer.old.content
    if (kind === 'binary') { answer.new.binary = true; answer.new.content = null }
    if (kind === 'too_large') { answer.new.truncated = true; answer.new.content = null }
    if (kind === 'long') { answer.old.content = ''; answer.new.content = Array.from({ length: 2501 }, (_, i) => `line ${i}`).join('\n') }
    await open({ activities: { [A]: [change('R1', [pathA])] }, diffs: { [`R1\n${pathA}`]: answer } })
    await openCard()
    expect(viewer().findAll('[data-test="rc-window-file"]')).toHaveLength(1)
    if (kind === 'empty' || kind === 'unchanged') expect(viewer().find('[data-test="rc-diff-empty"]').exists()).toBe(true)
    else if (kind === 'long') {
      expect(viewer().findAll('.rc-diff-line')).toHaveLength(2000)
      expect(viewer().find('[data-test="rc-diff-more"]').text()).toContain('501')
    } else expect(viewer().find('[data-test="rc-diff-unavailable"]').text()).toBe(i18n.global.t(`gui.conversation.diff.${kind}`))
    noInlineDiff()
  })

  it('keeps separate windows and stored content for V12 run cards, even for the same path', async () => {
    const first = change('R1', [pathA], 6)
    const second = change('R2', [pathA], 8)
    first.createdAt = '2026-10-06T09:00:00Z'
    second.createdAt = '2026-10-06T08:00:00Z'
    const { source, data } = await open({
      turns: { [A]: [turn(5, 'user', 'fix'), turn(6, 'ai', 'first', null, { sourceRunId: 'R1' }),
        turn(7, 'user', 'tests?'), turn(8, 'ai', 'second', null, { sourceRunId: 'R2' })] },
      activities: { [A]: [second, first] },
      diffs: { [`R1\n${pathA}`]: diff('R1', pathA, 'run one\n'), [`R2\n${pathA}`]: diff('R2', pathA, 'run two\n') },
    })
    const activitiesBefore = JSON.stringify(data.activities[A])
    const keys = session().timeline.value.filter((item) => item.kind !== 'boundary').map((item) => item.key)
    expect(keys).toEqual(['turn:5', 'turn:6', 'change:R1', 'turn:7', 'turn:8', 'change:R2'])
    await openCard(0)
    await openCard(1)
    expect(windows()).toHaveLength(2)
    expect(viewer('R1').text()).toContain('+run one')
    expect(viewer('R2').text()).toContain('+run two')
    const z2 = Number((shell('R2').element as HTMLElement).style.zIndex)
    await shell('R1').find('[data-modeless-action="minimize"]').trigger('click')
    await openCard(0)
    expect(shell('R1').classes()).not.toContain('is-minimized')
    expect(Number((shell('R1').element as HTMLElement).style.zIndex)).toBeGreaterThan(z2)
    expect(source.fetchRunFileDiff).toHaveBeenCalledTimes(2)
    expect(JSON.stringify(data.activities[A])).toBe(activitiesBefore)
    await shell('R1').find('[data-modeless-action="close"]').trigger('click')
    expect(viewer('R1').exists()).toBe(false)
    expect(viewer('R2').exists()).toBe(true)
    expect(session().timeline.value.filter((item) => item.kind !== 'boundary').map((item) => item.key)).toEqual(keys)
    noInlineDiff()
  })

  it('does not close on outside clicks, focus or a browser tab visibility change; Esc only inside closes it', async () => {
    await open()
    await openCard()
    const input = wrapper!.find('[data-test="conv-input"]').element as HTMLTextAreaElement
    input.focus()
    input.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    input.click()
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    const visibility = vi.spyOn(document, 'visibilityState', 'get')
    visibility.mockReturnValue('hidden')
    document.dispatchEvent(new Event('visibilitychange'))
    visibility.mockReturnValue('visible')
    document.dispatchEvent(new Event('visibilitychange'))
    window.dispatchEvent(new Event('focus'))
    await flushPromises()
    expect(windows()).toHaveLength(1)
    expect(document.activeElement).toBe(input)
    expect(body().find('.ui-modal-backdrop').exists()).toBe(false)
    await shell().trigger('keydown', { key: 'Escape' })
    expect(windows()).toHaveLength(0)
  })

  it('keeps the viewer when a mounted tool pane is hidden and shown by v-show', async () => {
    const { source } = await open()
    await openCard()
    const pane = wrapper!.element as HTMLElement
    pane.style.display = 'none' // DesignerRightDock retains mounted tools with v-show.
    await nextTick()
    expect(wrapper!.isVisible()).toBe(false)
    expect(viewer().isVisible()).toBe(true) // Teleport remains a separate, usable window.
    pane.style.display = ''
    await nextTick()
    expect(windows()).toHaveLength(1)
    expect(source.fetchRunFileDiff).toHaveBeenCalledTimes(1)
  })

  it('discards closed-window answers even if the same run and file are immediately reopened', async () => {
    const { source } = await open()
    const oldRead = deferred<RunFileDiff>()
    source.fetchRunFileDiff.mockImplementationOnce(() => oldRead.promise)
    await panelFile(pathA).trigger('click')
    await shell().find('[data-modeless-action="close"]').trigger('click')
    await panelFile(pathA).trigger('click')
    await flushPromises()
    oldRead.resolve(diff('R1', pathA, 'obsolete\n'))
    await flushPromises()
    expect(windows()).toHaveLength(1)
    expect(viewer().text()).toContain('+new')
    expect(viewer().text()).not.toContain('obsolete')
  })

  it('clears windows on A -> B -> A, rejects delayed reads, and recreates no windows on a page/panel remount', async () => {
    const fake = await open()
    const oldRead = deferred<RunFileDiff>()
    fake.source.fetchRunFileDiff.mockImplementationOnce(() => oldRead.promise)
    await panelFile(pathA).trigger('click')
    const oldSession = session()
    const b = oldSession.selectChat(B)
    const a = oldSession.selectChat(A)
    await Promise.all([a, b])
    await flushPromises()
    expect(windows()).toHaveLength(0)
    await openCard()
    oldRead.resolve(diff('R1', pathA, 'old chat lifetime\n'))
    await flushPromises()
    expect(viewer().text()).not.toContain('old chat lifetime')
    expect(viewer().text()).toContain('+new')
    wrapper!.unmount()
    wrapper = null
    expect(windows()).toHaveLength(0)
    await mountPanel(fake) // browser F5 / a tool-tab unmount starts a fresh panel lifetime
    expect(windows()).toHaveLength(0)
    expect(wrapper!.findAll('[data-test="tl-change"]')).toHaveLength(1)
    noInlineDiff()
  })

  it('closes on new-chat draft and scope change, but same-chat catch-up keeps the window', async () => {
    await open()
    await openCard()
    await session().selectChat(A)
    expect(windows()).toHaveLength(1)
    session().startDraft()
    await nextTick()
    expect(windows()).toHaveLength(0)
    await session().selectChat(A)
    await openCard()
    await wrapper!.setProps({ groupId: null })
    await flushPromises()
    expect(windows()).toHaveLength(0)
  })

  it('shows no card, window or diff request when there are no recorded changes', async () => {
    const { source } = await open({ activities: { [A]: [] } })
    expect(wrapper!.find('[data-test="tl-change"]').exists()).toBe(false)
    expect(windows()).toHaveLength(0)
    expect(source.fetchRunFileDiff).not.toHaveBeenCalled()
    noInlineDiff()
  })

  it('handles an empty file list without requesting a made-up diff', async () => {
    const { source } = createFakeSource()
    wrapper = mount(RunChangeWindow, { props: { chatId: A, activity: change('R1', []), index: 0,
      requestVersion: 0, loadDiff: source.fetchRunFileDiff }, global: { plugins: [i18n] } })
    await flushPromises()
    expect(viewer().find('[data-test="rc-window-empty"]').exists()).toBe(true)
    expect(source.fetchRunFileDiff).not.toHaveBeenCalled()
  })

  it('rejects a response for another run or path instead of displaying its code', async () => {
    const { source } = await open()
    source.fetchRunFileDiff.mockResolvedValueOnce(diff('wrong', pathA, 'foreign run\n'))
    await openCard()
    expect(viewer().find('[data-test="rc-diff-error"]').exists()).toBe(true)
    expect(viewer().text()).not.toContain('foreign run')
    source.fetchRunFileDiff.mockResolvedValueOnce(diff('R1', pathB, 'foreign file\n'))
    await viewer().find('[data-test="rc-window-retry"]').trigger('click')
    await flushPromises()
    expect(viewer().find('[data-test="rc-diff-error"]').exists()).toBe(true)
    expect(viewer().text()).not.toContain('foreign file')
  })

  it.each(['ko', 'en', 'ja'] as const)('uses the %s window title and shared diff wording', async (locale) => {
    i18n.global.locale.value = locale
    await open()
    await openCard()
    expect(viewer().attributes('aria-label')).toBe(i18n.global.t('gui.conversation.change.window_title', { run: 'R1' }))
    expect(shell().find('.ui-modeless-title').text()).not.toContain('gui.conversation')
  })
})
