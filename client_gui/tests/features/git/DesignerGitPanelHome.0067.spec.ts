import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import { DesignerGitPanel } from '@/features/git'
import type { WorktreeStatus } from '@/features/git/types'
import { G, P, createFakeGitSource } from './fakeGitSource'
import {
  createFakeWorktreeSource,
  manualMergeView,
  mergeResultRow,
  mergeResults,
  noDetails,
  worktreeFile,
  worktreeStatus,
  type FakeWorktreeState,
} from './fakeWorktreeSource'

// flowgate.v02.0067 T#1 — the Git tab home and the Git modeless (0067-R §2, §3, §7; v27): the
// summary line and four equal entries stacked one per row, one window for all four with its top
// tabs, tab context kept across switches, nothing on the Center canvas, and the `상태` tab.
registerAppMessages(i18n)

let wrapper: VueWrapper | null = null
let other: VueWrapper | null = null

const VIEWPORT = { width: window.innerWidth, height: window.innerHeight }

function setViewport(width: number, height: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width })
  Object.defineProperty(window, 'innerHeight', { configurable: true, writable: true, value: height })
  window.dispatchEvent(new Event('resize'))
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  other?.unmount()
  other = null
  setViewport(VIEWPORT.width, VIEWPORT.height)
  document.body.innerHTML = ''
})

const FLOW = { groupId: G, name: '인증 오류 조사' }

function setup(wt: Partial<FakeWorktreeState> = {}) {
  const fakeWt = createFakeWorktreeSource(wt)
  const fake = createFakeGitSource({}, { worktree: fakeWt.source })
  return { wt: fakeWt, fake }
}

async function mountPanel(t: ReturnType<typeof setup>, props: Record<string, unknown> = {}) {
  wrapper = mount(DesignerGitPanel, {
    props: { projectId: P, flow: FLOW, source: t.fake.source, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return wrapper
}

const q = (sel: string) => document.body.querySelector<HTMLElement>(`[data-test="${sel}"]`)
const qa = (sel: string) => [...document.body.querySelectorAll<HTMLElement>(sel)]
const text = (sel: string) => q(sel)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
const entries = () => qa('[data-test="git-worktree-entry"]')
const entry = (tab: string) => document.body.querySelector<HTMLElement>(`[data-test="git-worktree-entry"][data-tab="${tab}"]`)
const windowTab = (tab: string) => document.body.querySelector<HTMLElement>(`[data-test="git-window-tab"][data-tab="${tab}"]`)
const panel = (tab: string) => document.body.querySelector<HTMLElement>(`[data-test="git-window-panel"][data-tab="${tab}"]`)
const shown = (el: HTMLElement | null) => !!el && el.style.display !== 'none'
const shell = () => q('git-window')?.closest<HTMLElement>('.ui-modeless-panel') ?? null
const otherShell = () => qa('.ui-modeless-panel').find((p) => !p.contains(q('git-window'))) ?? null
const z = (el: HTMLElement | null) => Number(el?.style.zIndex ?? NaN)
const box = (el: HTMLElement) => {
  const [left, top, width, height] = [el.style.left, el.style.top, el.style.width, el.style.height].map((v) => parseFloat(v))
  return { left, top, width, height, right: left + width, bottom: top + height }
}
const disabled = (sel: string) => (q(sel) as HTMLButtonElement | null)?.disabled
const reads = (t: ReturnType<typeof setup>) =>
  [t.wt.source.loadStatus, t.wt.source.loadManualMerge, t.wt.source.loadMergeResults].map((m) => m.mock.calls.length)

async function click(target: string | HTMLElement | null) {
  const el = typeof target === 'string' ? q(target) : target
  if (!el) throw new Error(`missing ${String(target)}`)
  el.click()
  await flushPromises()
}

async function pressOther() {
  otherShell()!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
  await flushPromises()
}

async function typeMessage(value: string) {
  const message = q('git-commit-message') as HTMLInputElement
  message.value = value
  message.dispatchEvent(new Event('input'))
  await flushPromises()
}

const staged = (path: string) => worktreeFile(path, { staged: 'M', unstaged: null, canStage: false, canUnstage: true })

describe('Git tab home (0067-R §2)', () => {
  it('a summary of real counts and four entries — 파일 관리 / Base 반영 / Diff / 상태 — nothing else of the worktree', async () => {
    const files = [worktreeFile('src/a.ts'), worktreeFile('src/b.ts'), staged('src/c.ts')]
    const t = setup({ status: worktreeStatus({ files }), merge: manualMergeView({ aheadOfTarget: 4 }) })
    await mountPanel(t)
    expect([text('git-worktree-summary-changed'), text('git-worktree-summary-staged'), text('git-worktree-summary-merge')]).toEqual(['3', '1', '4'])
    expect(entries().map((e) => e.dataset.tab)).toEqual(['files', 'merge', 'diff', 'status'])
    expect(entries().map((e) => e.querySelector('.gw-entry-title')?.textContent)).toEqual(['파일 관리', 'Base 반영', 'Diff', '상태'])
    // 파일 관리: changed files; Base 반영: commits not in the target; Diff / 상태: no count.
    expect(entries().map((e) => e.querySelector('[data-test="git-worktree-entry-count"]')?.textContent)).toEqual(['3개', '4개 대기', '', ''])
    // Every entry has the same columns: icon, label, count, arrow.
    for (const e of entries()) {
      expect([...e.children].map((c) => c.getAttribute('class'))).toEqual(['gw-entry-icon', 'gw-entry-title', 'gw-entry-count', 'gw-entry-arrow'])
    }
    // No list, commit line, merge target, Diff body or detailed state in the tab.
    for (const sel of ['git-changes', 'git-commit-bar', 'git-apply', 'git-apply-info', 'git-status', 'git-window']) expect(q(sel), sel).toBeNull()
    expect(q('git-worktree')!.querySelector('input, select')).toBeNull()
  })

  it('0 everywhere: numbers only, no repeated "nothing" lines, Diff still without a count', async () => {
    const t = setup({ status: worktreeStatus({ files: [] }), merge: manualMergeView({ aheadOfTarget: 0, mergeNeeded: false }) })
    await mountPanel(t)
    expect([text('git-worktree-summary-changed'), text('git-worktree-summary-staged'), text('git-worktree-summary-merge')]).toEqual(['0', '0', '0'])
    expect(entries().map((e) => e.querySelector('[data-test="git-worktree-entry-count"]')?.textContent)).toEqual(['0개', '0개 대기', '', ''])
    expect(text('git-worktree')).not.toMatch(/Stage된 파일 없음|반영할 커밋 없음|변경 없음|0개$/)
  })

  it('stacks the four entries one per row, all the same size (CSS of the v27 menu rows)', () => {
    const css = readFileSync(join(resolve(__dirname, '../../..'), 'src/features/git/gitPanel.css'), 'utf8')
    const list = css.match(/\.gw-entries\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(list).toMatch(/grid-template-columns:\s*minmax\(0,\s*1fr\);/)
    const row = css.match(/\.gw-entry\s*\{([^}]*)\}/)?.[1] ?? ''
    expect(row).toMatch(/width:\s*100%/)
    expect(row).toMatch(/height:\s*47px/)
    expect(row).toMatch(/grid-template-columns:\s*20px minmax\(0,\s*1fr\) auto 12px/)
  })

  it('no workspace (W2): the entries stay without counts; the window says what is left', async () => {
    const t = setup({ status: worktreeStatus({ worktreeAvailable: false, files: [], worktreeToken: null, indexToken: null }) })
    await mountPanel(t)
    expect(text('git-worktree-unavailable')).toBe('작업 공간 없음(정리된 그룹)')
    expect(entries()).toHaveLength(4)
    expect(entries().map((e) => e.querySelector('[data-test="git-worktree-entry-count"]')?.textContent)).toEqual(['', '', '', ''])
    await click(entry('files'))
    expect(panel('files')!.querySelector('[data-test="git-window-unavailable"]')).not.toBeNull()
    expect(q('git-commit-bar')).toBeNull()
    await click(windowTab('merge'))
    expect(q('git-apply')).not.toBeNull()
  })
})

describe('Git modeless (0067-R §3)', () => {
  it('every entry opens the same one window, same size, on its own tab; the top tabs switch and mark the active one', async () => {
    const t = setup()
    const w = await mountPanel(t)
    const sizes: string[] = []
    for (const tab of ['files', 'merge', 'diff', 'status']) {
      await click(entry(tab))
      expect(qa('[data-test="git-window"]')).toHaveLength(1)
      expect(q('git-window')!.dataset.tab).toBe(tab)
      expect(shown(panel(tab))).toBe(true)
      expect(qa('[data-test="git-window-panel"]').filter(shown)).toHaveLength(1)
      sizes.push(`${shell()!.style.width} × ${shell()!.style.height}`)
    }
    expect(new Set(sizes)).toEqual(new Set(['700px × 560px']))
    expect(qa('[data-test="git-window-tab"]').map((b) => b.textContent?.trim())).toEqual(['파일 관리', 'Base 반영', 'Diff', '상태'])
    await click(windowTab('merge'))
    expect(qa('[data-test="git-window-tab"]').map((b) => [b.dataset.tab, b.getAttribute('aria-selected'), b.classList.contains('is-active')])).toEqual([
      ['files', 'false', false],
      ['merge', 'true', true],
      ['diff', 'false', false],
      ['status', 'false', false],
    ])
    expect(entry('merge')!.getAttribute('aria-pressed')).toBe('true')
    // Never the Center canvas: opening and switching raise no open-diff intent.
    expect(w.emitted('open-diff')).toBeUndefined()
  })

  it('a switch keeps the tab context — the selection, the typed message and the search', async () => {
    const t = setup({ status: worktreeStatus({ files: [worktreeFile('src/a.ts'), staged('src/b.ts')] }) })
    await mountPanel(t)
    await click(entry('files'))
    const search = q('git-changes-search-input') as HTMLInputElement
    search.value = 'src/'
    search.dispatchEvent(new Event('input'))
    await flushPromises()
    const select = document.body.querySelector<HTMLInputElement>('[data-test="git-row"][data-path="src/a.ts"] [data-test="git-row-select"]')!
    select.click()
    await flushPromises()
    const message = q('git-commit-message') as HTMLInputElement
    message.value = 'wip'
    message.dispatchEvent(new Event('input'))
    await flushPromises()
    await click(windowTab('status'))
    await click(windowTab('files'))
    expect((q('git-changes-search-input') as HTMLInputElement).value).toBe('src/')
    expect(text('git-selected-count')).toBe('선택 1개')
    expect((q('git-commit-message') as HTMLInputElement).value).toBe('wip')
  })

  it('←/→ on the tabs move between them', async () => {
    const t = setup()
    await mountPanel(t)
    await click(entry('status'))
    windowTab('status')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    await flushPromises()
    expect(q('git-window')!.dataset.tab).toBe('files')
    windowTab('files')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))
    await flushPromises()
    expect(q('git-window')!.dataset.tab).toBe('status')
  })

  it('the `Diff` tab reads the target list when shown, not before; closing lets the pane go', async () => {
    const t = setup()
    await mountPanel(t)
    await click(entry('files'))
    await click(windowTab('status'))
    expect(t.wt.source.loadTargetChanges).not.toHaveBeenCalled()
    await click(windowTab('diff'))
    expect(t.wt.source.loadTargetChanges).toHaveBeenCalledTimes(1)
    await click(shell()!.querySelector<HTMLElement>('button[aria-label="Close"]'))
    expect(q('git-window')).toBeNull()
    // Closed, a refresh reads no comparison list.
    await click('git-refresh')
    expect(t.wt.source.loadTargetChanges).toHaveBeenCalledTimes(1)
  })

  it('minimize keeps the window and its tab; an entry brings it back on that entry\'s tab', async () => {
    const t = setup()
    await mountPanel(t)
    await click(entry('merge'))
    await click(shell()!.querySelector<HTMLElement>('button[aria-label="Minimize"]'))
    expect(shell()!.classList.contains('is-minimized')).toBe(true)
    await click(entry('status'))
    expect(shell()!.classList.contains('is-minimized')).toBe(false)
    expect(q('git-window')!.dataset.tab).toBe('status')
  })

  it('a Flow switch closes the window; the gate closing takes it away', async () => {
    const t = setup()
    const w = await mountPanel(t)
    await click(entry('files'))
    await w.setProps({ flow: { groupId: 'p.m.0002', name: 'B' } })
    await flushPromises()
    expect(q('git-window')).toBeNull()
    await click(entry('files'))
    expect(q('git-window')).not.toBeNull()
    await w.setProps({ flow: null })
    await flushPromises()
    expect(q('git-window')).toBeNull()
  })
})

describe('Git modeless — opened again, in front, on screen (0067-R §3)', () => {
  it('closed and opened again, it reads the worktree again: files, counts, merge judgment and follow-up Push are the server values now', async () => {
    const t = setup({ status: worktreeStatus({ files: [worktreeFile('src/a.ts')] }), merge: manualMergeView({ aheadOfTarget: 1 }) })
    await mountPanel(t)
    await click(entry('files'))
    await click(windowTab('merge'))
    await click('git-apply-toggle')
    expect(text('git-apply-status')).toBe('1개 커밋 미반영')
    expect(disabled('git-apply-merge')).toBe(false)
    expect(q('git-apply-reason')).toBeNull()
    expect(q('git-result-push')).toBeNull()
    await click(shell()!.querySelector<HTMLElement>('button[aria-label="Close"]'))
    expect(q('git-window')).toBeNull()

    // Meanwhile, outside this screen and with the 0033 pending row unchanged: another file, a Stage,
    // a commit and a merge result waiting for its Push.
    t.wt.db.status = worktreeStatus({ files: [worktreeFile('src/a.ts'), staged('src/b.ts')], worktreeToken: 'wt-9', indexToken: 'ix-9' })
    t.wt.db.merge = manualMergeView({
      aheadOfTarget: 2,
      canMerge: false,
      blocked: [{ code: 'uncommitted_changes', reason: null, details: noDetails({ count: 2 }) }],
      uncommitted: { blocking: 2, counts: { unstaged: 1 }, protectedUncommitted: 0, reason: null },
    })
    t.wt.db.results = mergeResults({ results: [mergeResultRow()] })
    const before = reads(t)

    await click(entry('files'))
    reads(t).forEach((n, i) => expect(n).toBeGreaterThan(before[i]))
    expect([text('git-worktree-summary-changed'), text('git-worktree-summary-staged'), text('git-worktree-summary-merge')]).toEqual(['2', '1', '2'])
    expect(entries().map((e) => e.querySelector('[data-test="git-worktree-entry-count"]')?.textContent)).toEqual(['2개', '2개 대기', '', ''])
    expect(qa('[data-test="git-row"]').map((r) => r.dataset.path).sort()).toEqual(['src/a.ts', 'src/b.ts'])
    await click(windowTab('merge'))
    // The merge is blocked now and a merge result waits for its Push (the header word says so first).
    expect(disabled('git-apply-merge')).toBe(true)
    expect(text('git-apply-reason')).toContain('미커밋 변경 2개')
    expect(q('git-result-push')).not.toBeNull()
    expect(text('git-apply-status')).toBe('원격 미반영 1곳')
    await click(windowTab('status'))
    expect([text('git-status-changed'), text('git-status-staged'), text('git-status-ahead')]).toEqual(['2', '1', '2'])

    // Switching tabs reads nothing more.
    const settled = reads(t)
    await click(windowTab('files'))
    await click(windowTab('merge'))
    expect(reads(t)).toEqual(settled)
  })

  it('opened again on `Diff`, the comparison list is read once more with the rest', async () => {
    const t = setup()
    await mountPanel(t)
    await click(entry('diff'))
    expect(t.wt.source.loadTargetChanges).toHaveBeenCalledTimes(1)
    await click(shell()!.querySelector<HTMLElement>('button[aria-label="Close"]'))
    await click(entry('diff'))
    expect(t.wt.source.loadTargetChanges).toHaveBeenCalledTimes(2)
  })

  it('asked again by any entry, or restored, it comes in front of another modeless window', async () => {
    const t = setup()
    await mountPanel(t)
    await click(entry('files'))
    other = mount(ModelessShell, { props: { open: true, title: 'other' }, attachTo: document.body })
    await flushPromises()
    for (const tab of ['files', 'merge', 'diff', 'status']) {
      await pressOther()
      expect(z(shell()), tab).toBeLessThan(z(otherShell()))
      await click(entry(tab))
      expect(z(shell()), tab).toBeGreaterThan(z(otherShell()))
      expect(q('git-window')!.dataset.tab).toBe(tab)
    }
    // Minimized, the other in front: restored from its titlebar…
    await click(shell()!.querySelector<HTMLElement>('button[aria-label="Minimize"]'))
    await pressOther()
    expect(z(shell())).toBeLessThan(z(otherShell()))
    await click(shell()!.querySelector<HTMLElement>('button[aria-label="Restore"]'))
    expect(shell()!.classList.contains('is-minimized')).toBe(false)
    expect(z(shell())).toBeGreaterThan(z(otherShell()))
    // …or from an entry.
    await click(shell()!.querySelector<HTMLElement>('button[aria-label="Minimize"]'))
    await pressOther()
    await click(entry('merge'))
    expect(shell()!.classList.contains('is-minimized')).toBe(false)
    expect(z(shell())).toBeGreaterThan(z(otherShell()))
  })

  it('on a 640 × 480 screen it opens inside it: titlebar controls, tabs and the body all reachable', async () => {
    setViewport(640, 480)
    const t = setup()
    await mountPanel(t)
    await click(entry('files'))
    const b = box(shell()!)
    expect([b.left >= 0, b.top >= 0, b.right <= 640, b.bottom <= 480]).toEqual([true, true, true, true])
    expect(b.width).toBeLessThan(700)
    expect(b.height).toBeLessThan(560)
    for (const label of ['Minimize', 'Close']) expect(shell()!.querySelector(`button[aria-label="${label}"]`), label).not.toBeNull()
    expect(qa('[data-test="git-window-tab"]')).toHaveLength(4)
    expect(q('git-commit-bar')).not.toBeNull()
    // The tab row stays; only the body scrolls inside the smaller window.
    const css = readFileSync(join(resolve(__dirname, '../../..'), 'src/features/git/gitPanel.css'), 'utf8')
    expect(css.match(/\.gw-win\s*\{([^}]*)\}/)?.[1] ?? '').toMatch(/height:\s*100%/)
    expect(css.match(/\.gw-win-tabs\s*\{([^}]*)\}/)?.[1] ?? '').toMatch(/flex:\s*none/)
    expect(css.match(/\.gw-win-body\s*\{([^}]*)\}/)?.[1] ?? '').toMatch(/overflow:\s*auto/)
  })

  it('a normal screen keeps 700 × 560; made smaller while open it shrinks and moves inside, keeping its tab and input; restored, it fits', async () => {
    setViewport(1280, 800)
    // flowgate.v02.0067 T#2: the message opens once something is staged.
    const t = setup({ status: worktreeStatus({ files: [staged('src/a.ts')] }) })
    await mountPanel(t)
    await click(entry('files'))
    expect(box(shell()!)).toMatchObject({ left: 200, top: 88, width: 700, height: 560 })
    await typeMessage('wip')

    setViewport(640, 480)
    await flushPromises()
    let b = box(shell()!)
    expect([b.left >= 0, b.top >= 0, b.right <= 640, b.bottom <= 480]).toEqual([true, true, true, true])
    expect(q('git-window')!.dataset.tab).toBe('files')
    expect((q('git-commit-message') as HTMLInputElement).value).toBe('wip')

    await click(shell()!.querySelector<HTMLElement>('button[aria-label="Minimize"]'))
    setViewport(480, 360)
    await click(shell()!.querySelector<HTMLElement>('button[aria-label="Restore"]'))
    b = box(shell()!)
    expect([b.left >= 0, b.top >= 0, b.right <= 480, b.bottom <= 360]).toEqual([true, true, true, true])
    expect((q('git-commit-message') as HTMLInputElement).value).toBe('wip')

    // Closed and opened again on a normal screen: the v27 size and place again.
    await click(shell()!.querySelector<HTMLElement>('button[aria-label="Close"]'))
    setViewport(1280, 800)
    await click(entry('status'))
    expect(box(shell()!)).toMatchObject({ left: 200, top: 88, width: 700, height: 560 })
  })
})

describe('`상태` tab (0067-R §7)', () => {
  it('three numbers and four rows from the session — never a fixed branch', async () => {
    const status: WorktreeStatus = worktreeStatus({ branch: 'flowgate_v02_0042', files: [worktreeFile('a.ts'), staged('b.ts')] })
    const t = setup({ status, merge: manualMergeView({ aheadOfTarget: 0, mergeNeeded: false }) })
    await mountPanel(t)
    await click(entry('status'))
    expect([text('git-status-changed'), text('git-status-staged'), text('git-status-ahead')]).toEqual(['2', '1', '0'])
    expect(text('git-status-worktree')).toBe('flowgate_v02_0042')
    expect(text('git-status-target')).toBe('v0.2 (기본)')
    expect(text('git-status-state')).toBe('반영할 커밋 없음')
    expect(qa('[data-test="git-status"] dt').map((d) => d.textContent)).toEqual(['Worktree', '기준 브랜치', '머지 대상', '상태'])
  })
})
