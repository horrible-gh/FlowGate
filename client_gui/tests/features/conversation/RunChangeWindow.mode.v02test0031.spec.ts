import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { DOMWrapper, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { APP_MESSAGES, registerAppMessages } from '@/app/i18n'
import RunChangeWindow from '@/features/conversation/RunChangeWindow.vue'
import type { RunFileDiff, WorkChangeActivity } from '@/features/conversation/types'

// flowgate.v02test.0031 B (0007-T): the change window's window-local `변경 / 전체` view mode.
registerAppMessages(i18n)
const CHAT = 'p.m.0001.0002-CH'
const pathA = 'src/a.ts'
const pathB = 'src/b.ts'
let wrapper: VueWrapper | null = null

function change(runId = 'R1', paths = [pathA, pathB]): WorkChangeActivity {
  return { kind: 'change', id: runId, runId, createdAt: null,
    files: paths.map((path) => ({ path, status: 'M', insertions: 1, deletions: 1 })),
    filesChanged: paths.length, insertions: paths.length, deletions: paths.length,
    anchor: { seq: 6, position: 'after', state: 'reply' } }
}
function diff(runId: string, path: string, oldText: string | null, newText: string | null): RunFileDiff {
  return { runId, path, status: 'M',
    old: { exists: oldText !== null, binary: false, truncated: false, size: oldText?.length ?? 0, content: oldText },
    new: { exists: newText !== null, binary: false, truncated: false, size: newText?.length ?? 0, content: newText } }
}
type Answers = Record<string, RunFileDiff | Error>
function loader(answers: Answers) {
  return vi.fn(async (_chat: string, runId: string, path: string) => {
    const answer = answers[`${runId}\n${path}`]
    if (!answer) throw new Error('missing fixture')
    if (answer instanceof Error) throw answer
    return structuredClone(answer)
  })
}
const DEFAULT: Answers = {
  [`R1\n${pathA}`]: diff('R1', pathA, 'keep 1\nold\nkeep 2\n', 'keep 1\nnew\nkeep 2\n'),
  [`R1\n${pathB}`]: diff('R1', pathB, 'b old\n', 'b new\n'),
  [`R2\n${pathA}`]: diff('R2', pathA, 'r2 old\n', 'r2 new\n'),
}
async function mountWindow(answers: Answers = DEFAULT, activity = change()) {
  const loadDiff = loader(answers)
  wrapper = mount(RunChangeWindow, { props: { chatId: CHAT, activity, index: 0, requestVersion: 0, loadDiff },
    global: { plugins: [i18n] } })
  await flushPromises()
  return loadDiff
}
const body = () => new DOMWrapper(document.body)
const viewer = () => body().find('[data-test="rc-window"]')
const q = (sel: string) => viewer().find(`[data-test="${sel}"]`)
const row = (path: string) => viewer().find(`[data-test="rc-window-file"][data-path="${path}"]`)
const fullLines = () => viewer().findAll('[data-test="rc-diff-full-line"]')
const modeOf = () => q('rc-diff').attributes('data-mode')
async function click(sel: string) {
  await q(sel).trigger('click')
  await flushPromises()
}
function expectMode(mode: 'changes' | 'full') {
  expect(q('rc-mode-changes').attributes('aria-pressed')).toBe(String(mode === 'changes'))
  expect(q('rc-mode-full').attributes('aria-pressed')).toBe(String(mode === 'full'))
  expect(modeOf()).toBe(mode)
}

beforeEach(() => { i18n.global.locale.value = 'ko' })
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('v02test.0031 B change window view mode', () => {
  it('opens in changes mode with the unchanged diff rendering', async () => {
    await mountWindow()
    expectMode('changes')
    expect(q('rc-mode-changes').text()).toBe('변경')
    expect(q('rc-mode-full').text()).toBe('전체')
    const text = q('rc-diff-lines').text()
    expect(text).toContain('-old')
    expect(text).toContain('+new')
    expect(q('rc-diff-full').exists()).toBe(false)
    expect(q('rc-diff-basis').exists()).toBe(false)
  })

  it('full shows the stored run end text with line numbers, the after-run basis and no add/del colour, without another read', async () => {
    const loadDiff = await mountWindow()
    expect(loadDiff).toHaveBeenCalledTimes(1)
    await click('rc-mode-full')
    expectMode('full')
    expect(q('rc-diff-basis').text()).toBe('실행 후')
    expect(q('rc-diff-basis').attributes('data-basis')).toBe('after')
    expect(fullLines().map((line) => line.text())).toEqual(['1keep 1', '2new', '3keep 2'])
    expect(fullLines().map((line) => line.find('.rc-diff-no').text())).toEqual(['1', '2', '3'])
    expect(q('rc-diff-full').text()).not.toContain('old')
    expect(viewer().find('.rc-diff-line--add').exists()).toBe(false)
    expect(viewer().find('.rc-diff-line--del').exists()).toBe(false)
    expect(q('rc-diff-lines').exists()).toBe(false)
    await click('rc-mode-changes')
    expectMode('changes')
    expect(q('rc-diff-lines').text()).toContain('+new')
    await click('rc-mode-full')
    expect(loadDiff).toHaveBeenCalledTimes(1)
  })

  it('full shows the run start text with the before-delete basis for a file the run deleted', async () => {
    const deleted = diff('R1', pathA, 'gone 1\r\ngone 2\r\n', null)
    deleted.status = 'D'
    await mountWindow({ [`R1\n${pathA}`]: deleted }, change('R1', [pathA]))
    await click('rc-mode-full')
    expect(q('rc-diff-basis').text()).toBe('삭제 전')
    expect(q('rc-diff-basis').attributes('data-basis')).toBe('before_delete')
    expect(fullLines().map((line) => line.text())).toEqual(['1gone 1', '2gone 2'])
  })

  it('keeps the mode across file selection and reads each file once', async () => {
    const loadDiff = await mountWindow()
    await click('rc-mode-full')
    await row(pathB).trigger('click')
    await flushPromises()
    expect(row(pathB).attributes('aria-pressed')).toBe('true')
    expectMode('full')
    expect(fullLines().map((line) => line.text())).toEqual(['1b new'])
    await row(pathA).trigger('click')
    await flushPromises()
    expectMode('full')
    await click('rc-mode-changes')
    await click('rc-mode-full')
    expect(loadDiff.mock.calls).toEqual([[CHAT, 'R1', pathA], [CHAT, 'R1', pathB]])
  })

  it('starts a new window instance in changes mode', async () => {
    await mountWindow()
    await click('rc-mode-full')
    expectMode('full')
    wrapper!.unmount()
    wrapper = null
    await mountWindow()
    expectMode('changes')
  })

  it('keeps the chosen mode when the same window is invalidated by a run change', async () => {
    const loadDiff = await mountWindow()
    await click('rc-mode-full')
    await wrapper!.setProps({ activity: change('R2', [pathA]) })
    await flushPromises()
    expect(loadDiff.mock.calls.at(-1)).toEqual([CHAT, 'R2', pathA])
    expectMode('full')
    expect(fullLines().map((line) => line.text())).toEqual(['1r2 new'])
  })

  it('pages a full text over 2,000 lines with show more and resets the page on file or mode change', async () => {
    const big = Array.from({ length: 4500 }, (_, i) => `line ${i + 1}`).join('\n')
    await mountWindow({
      [`R1\n${pathA}`]: diff('R1', pathA, '', big),
      [`R1\n${pathB}`]: diff('R1', pathB, 'b old\n', 'b new\n'),
    })
    await click('rc-mode-full')
    expect(fullLines()).toHaveLength(2000)
    expect(q('rc-diff-more').text()).toContain('2500')
    expect(q('rc-diff-show-more').text()).toBe('더 보기')
    await click('rc-diff-show-more')
    expect(fullLines()).toHaveLength(4000)
    expect(q('rc-diff-more').text()).toContain('500')
    await click('rc-diff-show-more')
    expect(fullLines()).toHaveLength(4500)
    expect(fullLines().at(-1)!.text()).toBe('4500line 4500')
    expect(q('rc-diff-show-more').exists()).toBe(false)
    expect(q('rc-diff-more').exists()).toBe(false)
    await click('rc-mode-changes')
    await click('rc-mode-full')
    expect(fullLines()).toHaveLength(2000)
    await click('rc-diff-show-more')
    expect(fullLines()).toHaveLength(4000)
    await row(pathB).trigger('click')
    await flushPromises()
    await row(pathA).trigger('click')
    await flushPromises()
    expect(fullLines()).toHaveLength(2000)
  })

  it.each(['binary', 'too_large', 'error', 'missing_text', 'loading'] as const)('shows no original text in full mode for %s', async (kind) => {
    const answer = diff('R1', pathA, 'secret old\n', 'secret new\n')
    if (kind === 'binary') answer.new.binary = true
    if (kind === 'too_large') answer.new.truncated = true
    if (kind === 'missing_text') answer.new.content = null
    const answers: Answers = { [`R1\n${pathA}`]: kind === 'error' ? new Error('boom') : answer }
    const loadDiff = await mountWindow(answers, change('R1', [pathA]))
    if (kind === 'loading') {
      loadDiff.mockImplementationOnce(() => new Promise<RunFileDiff>(() => {}))
      await wrapper!.setProps({ activity: change('R9', [pathA]) })
      await flushPromises()
    }
    await click('rc-mode-full')
    expectMode('full')
    expect(q('rc-diff-full').exists()).toBe(false)
    expect(q('rc-diff-basis').exists()).toBe(false)
    expect(viewer().text()).not.toContain('secret')
    if (kind === 'binary' || kind === 'too_large') {
      expect(q('rc-diff-unavailable').text()).toBe(i18n.global.t(`gui.conversation.diff.${kind}`))
    } else if (kind === 'loading') {
      expect(q('rc-diff-loading').exists()).toBe(true)
    } else {
      expect(q('rc-diff-error').exists()).toBe(true)
    }
  })

  it.each(['ko', 'en', 'ja'] as const)('has every %s mode / projection key, keeping the existing ones', (locale) => {
    const diffMessages = (APP_MESSAGES[locale].conversation as unknown as { diff: Record<string, string> }).diff
    for (const key of ['loading', 'failed', 'binary', 'too_large', 'empty', 'more_lines',
      'mode_label', 'mode_changes', 'mode_full', 'full_basis_after', 'full_basis_before_delete',
      'show_more', 'omitted_lines', 'alignment_limit', 'remaining_changes']) {
      expect(typeof diffMessages[key], `${locale}.${key}`).toBe('string')
      expect(diffMessages[key].length, `${locale}.${key}`).toBeGreaterThan(0)
    }
    i18n.global.locale.value = locale
    expect(i18n.global.t('gui.conversation.diff.omitted_lines', { n: 7 })).toContain('7')
    expect(i18n.global.t('gui.conversation.diff.remaining_changes', { n: 9 })).toContain('9')
    expect(i18n.global.t('gui.conversation.diff.alignment_limit')).toContain(i18n.global.t('gui.conversation.diff.mode_full'))
  })

  it('prints stored text only through interpolation', () => {
    for (const name of ['RunChangeDiff.vue', 'RunChangeWindow.vue']) {
      const src = readFileSync(resolve(__dirname, '../../../src/features/conversation', name), 'utf8')
      expect(src, name).not.toMatch(/v-html|innerHTML/)
    }
  })
})
