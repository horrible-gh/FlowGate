import { DOMWrapper, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import RunChangeDiff from '@/features/conversation/RunChangeDiff.vue'
import RunChangeWindow from '@/features/conversation/RunChangeWindow.vue'
import * as comparison from '@/features/conversation/changeDiff'
import type { OpenFileDiff } from '@/features/conversation/changeDiff'
import type { RunFileDiff, WorkChangeActivity } from '@/features/conversation/types'

registerAppMessages(i18n)
let wrapper: VueWrapper | null = null
const text = (n: number) => Array.from({ length: n }, (_, i) => `line ${i + 1}`).join('\n')
function fixture(oldText: string | null, newText: string | null, path = 'a.ts'): RunFileDiff {
  return { runId: 'R1', path, status: oldText === null ? 'A' : newText === null ? 'D' : 'M',
    old: { exists: oldText !== null, content: oldText, binary: false, truncated: false, size: oldText?.length ?? 0 },
    new: { exists: newText !== null, content: newText, binary: false, truncated: false, size: newText?.length ?? 0 } }
}
function state(diff: RunFileDiff): OpenFileDiff {
  return { runId: diff.runId, path: diff.path, status: 'ready', diff, error: null }
}
function render(diff: RunFileDiff) {
  wrapper = mount(RunChangeDiff, { props: { fileDiff: state(diff) }, global: { plugins: [i18n] } })
  return wrapper
}
beforeEach(() => { i18n.global.locale.value = 'ko' })
afterEach(() => { wrapper?.unmount(); wrapper = null; document.body.innerHTML = ''; vi.restoreAllMocks() })

describe('v02test.0031 integrated context view', () => {
  it.each([[100, 50, 46, 47], [3000, 2500, 2496, 497]])('projects %i lines around change %i before limiting the DOM', (n, changed, before, after) => {
    const old = text(n)
    const w = render(fixture(old, old.replace(`line ${changed}\n`, 'replacement\n')))
    expect(w.attributes('data-mode')).toBe('changes')
    expect(w.findAll('.rc-diff-line')).toHaveLength(8)
    expect(w.findAll('[data-kind="gap"]').map((r) => r.text())).toEqual([`… ${before}줄 생략`, `… ${after}줄 생략`])
    const del = w.get('[data-kind="del"]')
    const add = w.get('[data-kind="add"]')
    expect(del.text()).toContain(`-line ${changed}`)
    expect(add.text()).toContain('+replacement')
    expect(del.get('[data-side="old"]').text()).toBe(String(changed))
    expect(del.get('[data-side="new"]').text()).toBe('')
    expect(add.get('[data-side="old"]').text()).toBe('')
    expect(add.get('[data-side="new"]').text()).toBe(String(changed))
    expect(w.find('[data-test="rc-diff-more"]').exists()).toBe(false)
    expect(w.find('[data-test="rc-diff-alignment-limit"]').exists()).toBe(false)
  })

  it('shows alignment fallback separately from paging and hides it in full mode', async () => {
    vi.spyOn(comparison, 'diffLinesDetailed').mockReturnValue({ aligned: false, lines: [
      { kind: 'del', text: 'old', oldNo: 1, newNo: null },
      { kind: 'add', text: 'new', oldNo: null, newNo: 1 },
    ] })
    const w = render(fixture('old', 'new'))
    expect(w.get('[data-test="rc-diff-alignment-limit"]').text()).toBe(i18n.global.t('gui.conversation.diff.alignment_limit'))
    expect(w.find('[data-test="rc-diff-more"]').exists()).toBe(false)
    await w.setProps({ mode: 'full' })
    expect(w.find('[data-test="rc-diff-alignment-limit"]').exists()).toBe(false)
    expect(w.get('[data-test="rc-diff-full"]').text()).toContain('new')
  })

  it('counts only hidden changes, pages projected rows including gaps, and resets on mode/file changes', async () => {
    // 1995 additions + three context rows + gap + three context rows + final addition.
    const middle = text(20)
    const added = Array.from({ length: 1995 }, (_, i) => `added ${i}`).join('\n')
    const diff = fixture(middle, `${added}\n${middle}\nlast addition`)
    const w = render(diff)
    expect(w.findAll('.rc-diff-line')).toHaveLength(1999)
    expect(w.findAll('[data-kind="gap"]')).toHaveLength(1)
    expect(w.get('[data-test="rc-diff-more"]').text()).toContain(i18n.global.t('gui.conversation.diff.remaining_changes', { n: 1 }))
    await w.get('[data-test="rc-diff-show-more"]').trigger('click')
    expect(w.findAll('[data-kind="add"]')).toHaveLength(1996)
    expect(w.get('[data-test="rc-diff-lines"]').text()).toContain('+last addition')
    expect(w.find('[data-test="rc-diff-more"]').exists()).toBe(false)
    await w.setProps({ mode: 'full' })
    await w.setProps({ mode: 'changes' })
    expect(w.findAll('.rc-diff-line')).toHaveLength(1999)
    await w.get('[data-test="rc-diff-show-more"]').trigger('click')
    await w.setProps({ fileDiff: state({ ...diff, path: 'b.ts' }) })
    expect(w.findAll('.rc-diff-line')).toHaveLength(1999)
  })

  it.each(['add', 'del'] as const)('preserves all %s rows through successive pages', async (kind) => {
    const w = render(kind === 'add' ? fixture(null, text(4500)) : fixture(text(4500), null))
    expect(w.findAll(`[data-kind="${kind}"]`)).toHaveLength(2000)
    expect(w.get('[data-test="rc-diff-more"]').text()).toContain('2500')
    await w.get('[data-test="rc-diff-show-more"]').trigger('click')
    expect(w.findAll(`[data-kind="${kind}"]`)).toHaveLength(4000)
    await w.get('[data-test="rc-diff-show-more"]').trigger('click')
    expect(w.findAll(`[data-kind="${kind}"]`)).toHaveLength(4500)
    expect(w.find('[data-test="rc-diff-more"]').exists()).toBe(false)
    await w.setProps({ mode: 'full' })
    expect(w.get('[data-test="rc-diff-basis"]').attributes('data-basis')).toBe(kind === 'del' ? 'before_delete' : 'after')
  })

  it.each(['unchanged', 'empty', 'binary', 'truncated', 'loading', 'error'] as const)('preserves the %s notice', async (kind) => {
    const diff = fixture(kind === 'empty' ? '' : 'same', kind === 'empty' ? '' : 'same')
    if (kind === 'binary') diff.new.binary = true
    if (kind === 'truncated') diff.new.truncated = true
    const w = render(diff)
    if (kind === 'loading' || kind === 'error') await w.setProps({ fileDiff: { ...state(diff), status: kind } })
    const notice = kind === 'binary' || kind === 'truncated' ? 'unavailable' : kind === 'loading' || kind === 'error' ? kind : 'empty'
    expect(w.find(`[data-test="rc-diff-${notice}"]`).exists()).toBe(true)
    expect(w.findAll('.rc-diff-line')).toHaveLength(0)
  })

  it('escapes stored HTML in both modes', async () => {
    const html = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    const w = render(fixture('old', html))
    for (const mode of ['changes', 'full'] as const) {
      await w.setProps({ mode })
      expect(w.text()).toContain(html)
      expect(w.find('img').exists()).toBe(false)
      expect(w.find('script').exists()).toBe(false)
    }
  })

  it('integrates window toggles and file selection with projection and the existing cache', async () => {
    const old = text(100)
    const loadDiff = vi.fn(async (_chat: string, _run: string, path: string) => fixture(old, old.replace('line 50\n', `${path} changed\n`), path))
    const activity: WorkChangeActivity = { kind: 'change', id: 'R1', runId: 'R1', createdAt: null,
      files: ['a.ts', 'b.ts'].map((path) => ({ path, status: 'M', insertions: 1, deletions: 1 })),
      filesChanged: 2, insertions: 2, deletions: 2, anchor: { seq: 1, position: 'after', state: 'reply' } }
    wrapper = mount(RunChangeWindow, { props: { chatId: 'p.m.0001.0002-CH', activity, index: 0, requestVersion: 0, loadDiff }, global: { plugins: [i18n] } })
    await flushPromises()
    const body = new DOMWrapper(document.body)
    expect(body.findAll('.rc-diff-line')).toHaveLength(8)
    await body.get('[data-test="rc-mode-full"]').trigger('click')
    expect(body.findAll('[data-test="rc-diff-full-line"]')).toHaveLength(100)
    await body.get('[data-test="rc-window-file"][data-path="b.ts"]').trigger('click')
    await flushPromises()
    expect(body.get('[data-test="rc-diff"]').attributes('data-mode')).toBe('full')
    await body.get('[data-test="rc-mode-changes"]').trigger('click')
    expect(body.findAll('.rc-diff-line')).toHaveLength(8)
    expect(body.get('[data-kind="add"]').text()).toContain('+b.ts changed')
    await body.get('[data-test="rc-window-file"][data-path="a.ts"]').trigger('click')
    await flushPromises()
    expect(body.get('[data-test="rc-diff"]').attributes('data-mode')).toBe('changes')
    expect(body.findAll('.rc-diff-line')).toHaveLength(8)
    expect(loadDiff).toHaveBeenCalledTimes(2)
  })
})
