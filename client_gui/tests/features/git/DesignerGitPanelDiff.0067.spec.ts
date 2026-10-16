import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import type { FlowChangeSet, FlowFileDiff } from '@/features/project'
import type { ManualMergeView } from '@/features/git/types'
import { DesignerGitPanel } from '@/features/git'
import { diffListPage, WORKTREE_DIFF_PAGE } from '@/features/git/worktreeList'
import { G, P, createFakeGitSource, gitError, networkError } from './fakeGitSource'
import {
  createFakeWorktreeSource,
  fileDiff,
  manualMergeView,
  worktreeFile,
  worktreeStatus,
  type FakeWorktreeState,
} from './fakeWorktreeSource'

// flowgate.v02.0067 T#4 — the `Diff` tab of the Git modeless (0067-R §6, v27 `compareBase()` /
// `workDiff()`) in the real DesignerGitPanel with in-memory sources: the comparison list first, with a
// path search and pages (§6-1); a file opens in the same window and `목록으로` goes back to the list
// it came from — `파일 관리` when it was opened there (§6-2); `파일 경로` with the full path once and
// `비교 기준` on its own (§6-3); the code scrolls sideways inside its area (§6-4); no difference and a
// failed read are different lines, the reads are the existing ones (§6-5). The Center Diff session's
// rules stay: a worktree change reads the open file again, a target change closes a list file.
registerAppMessages(i18n)

let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

const FLOW = { groupId: G, name: '인증 오류 조사' }

const list = (target: string, paths: string[], letter = 'M'): FlowChangeSet => ({
  groupId: G,
  branch: 'flowgate_v02_0008',
  commit: 'h1',
  baseBranch: target,
  changes: paths.map((path) => ({ path, status: letter })),
})

const many = (n: number, prefix = 'src/f') => Array.from({ length: n }, (_, i) => `${prefix}${String(i).padStart(2, '0')}.ts`)

function setup(wt: Partial<FakeWorktreeState> = {}) {
  const fakeWt = createFakeWorktreeSource({
    status: worktreeStatus({ files: [worktreeFile('src/a.ts'), worktreeFile('src/b.ts'), worktreeFile('lib/c.ts')] }),
    targetChanges: { 'v0.2': list('v0.2', ['src/a.ts', 'src/b.ts', 'lib/c.ts']) },
    ...wt,
  })
  const fake = createFakeGitSource({}, { worktree: fakeWt.source })
  return { wt: fakeWt, fake }
}

async function mountPanel(t: ReturnType<typeof setup>, entry: 'diff' | 'files' = 'diff') {
  wrapper = mount(DesignerGitPanel, {
    props: { projectId: P, flow: FLOW, source: t.fake.source },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  await click(document.body.querySelector<HTMLElement>(`[data-test="git-worktree-entry"][data-tab="${entry}"]`))
  return wrapper
}

const q = (sel: string) => document.body.querySelector<HTMLElement>(`[data-test="${sel}"]`)
const qa = (sel: string) => [...document.body.querySelectorAll<HTMLElement>(sel)]
const text = (sel: string) => q(sel)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
const disabled = (sel: string) => (q(sel) as HTMLButtonElement | null)?.disabled
const rows = () => qa('[data-test="git-apply-diff-row"]').map((r) => r.dataset.path)
const tab = () => q('git-window')?.dataset.tab
const shown = (el: HTMLElement | null) => !!el && el.style.display !== 'none'

async function click(target: string | HTMLElement | null | undefined) {
  const el = typeof target === 'string' ? q(target) : target
  if (!el) throw new Error(`missing ${String(target)}`)
  el.click()
  await flushPromises()
}

async function type(sel: string, value: string) {
  const el = q(sel) as HTMLInputElement | null
  if (!el) throw new Error(`missing ${sel}`)
  el.value = value
  el.dispatchEvent(new Event('input'))
  await flushPromises()
}

async function openTab(name: string) {
  await click(document.body.querySelector<HTMLElement>(`[data-test="git-window-tab"][data-tab="${name}"]`))
}

const row = (path: string) => qa('[data-test="git-apply-diff-row"]').find((r) => r.dataset.path === path) ?? null
const fileRow = (path: string, sel: string) =>
  document.body.querySelector<HTMLElement>(`[data-test="git-row"][data-path="${path}"] [data-test="${sel}"]`)

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => (resolve = res))
  return { promise, resolve }
}

const css = () => readFileSync(join(resolve(__dirname, '../../..'), 'src/features/git/gitPanel.css'), 'utf8')
const rule = (selector: string) => {
  const escaped = selector.replace(/[.]/g, '\\.').replace(/,\s*/g, ',\\s*')
  return css().match(new RegExp(`(?:^|[}/])\\s*${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? ''
}

describe('`Diff` tab — the comparison list first (R §6-1)', () => {
  it('entering shows the chosen target list: its count, a search, full-path rows and the pages — no detail', async () => {
    const t = setup()
    await mountPanel(t)
    expect(tab()).toBe('diff')
    expect(q('git-center-diff')).toBeNull()
    expect(text('git-apply-diff-title')).toBe('v0.2 기준 변경 3개')
    expect((q('git-apply-diff-search') as HTMLInputElement).placeholder).toBe('파일 검색')
    expect(q('git-apply-diff-search')?.getAttribute('aria-label')).toBe('비교할 파일 검색')
    expect(rows()).toEqual(['src/a.ts', 'src/b.ts', 'lib/c.ts'])
    expect(row('lib/c.ts')?.querySelector('.gw-mark')?.textContent).toBe('M')
    expect(row('lib/c.ts')?.querySelector('.gw-row-name')?.textContent).toBe('lib/c.ts')
    expect(row('lib/c.ts')?.getAttribute('title')).toBe('lib/c.ts')
    expect(text('git-apply-diff-range')).toBe('1–3 / 3')
    expect([disabled('git-apply-diff-prev'), disabled('git-apply-diff-next')]).toEqual([true, true])
    expect(t.wt.source.loadTargetChanges).toHaveBeenCalledWith(P, G, 'v0.2')
    expect(t.wt.source.loadFileDiff).not.toHaveBeenCalled()
  })

  it('search: part of the path in any case, back to the first page; no match is its own line', async () => {
    const t = setup({ targetChanges: { 'v0.2': list('v0.2', [...many(20, 'src/a'), ...many(20, 'lib/B')]) } })
    await mountPanel(t)
    expect(rows()).toHaveLength(WORKTREE_DIFF_PAGE)
    await click('git-apply-diff-next')
    expect(text('git-apply-diff-range')).toBe('17–32 / 40')
    await type('git-apply-diff-search', 'LIB/b1')
    expect(rows()).toEqual(many(20, 'lib/B').slice(10, 20))
    expect(text('git-apply-diff-range')).toBe('1–10 / 10')
    expect([disabled('git-apply-diff-prev'), disabled('git-apply-diff-next')]).toEqual([true, true])
    await type('git-apply-diff-search', 'nothing-like-this')
    expect(rows()).toEqual([])
    expect(text('git-apply-diff-no-match')).toBe('일치하는 파일 없음')
    expect(text('git-apply-diff-range')).toBe('0–0 / 0')
    // The count line is the list's own, not the search's.
    expect(text('git-apply-diff-title')).toBe('v0.2 기준 변경 40개')
    expect(t.wt.source.loadTargetChanges).toHaveBeenCalledTimes(1)
  })

  it('an empty list says 변경 없음 once, without search or pages', async () => {
    const t = setup({ targetChanges: { 'v0.2': list('v0.2', []) } })
    await mountPanel(t)
    expect(text('git-apply-diff-empty')).toBe('변경 없음')
    expect(q('git-apply-diff-search')).toBeNull()
    expect(q('git-apply-diff-pages')).toBeNull()
  })
})

describe('`Diff` tab — the file detail (R §6-2–§6-5)', () => {
  it('a list row opens in the same window; `목록으로` comes back to the same search and page', async () => {
    const t = setup({ targetChanges: { 'v0.2': list('v0.2', many(40)) } })
    await mountPanel(t)
    await type('git-apply-diff-search', 'src/f')
    await click('git-apply-diff-next')
    expect(text('git-apply-diff-range')).toBe('17–32 / 40')
    await click(row('src/f20.ts'))
    expect(tab()).toBe('diff')
    expect(q('git-center-diff')?.dataset.path).toBe('src/f20.ts')
    expect(shown(q('git-apply-info'))).toBe(false)
    expect(t.wt.source.loadFileDiff.mock.calls).toEqual([[P, G, 'src/f20.ts', 'v0.2']])
    expect(text('git-center-diff-compare')).toBe('v0.2 기준 ↔ 작업 파일')
    await click('git-center-diff-back')
    expect(q('git-center-diff')).toBeNull()
    expect(tab()).toBe('diff')
    expect(shown(q('git-apply-info'))).toBe(true)
    expect((q('git-apply-diff-search') as HTMLInputElement).value).toBe('src/f')
    expect(text('git-apply-diff-range')).toBe('17–32 / 40')
    // Nothing was read again for the list.
    expect(t.wt.source.loadTargetChanges).toHaveBeenCalledTimes(1)
  })

  it('a `파일 관리` row opens the `Diff` tab; `목록으로` goes back to `파일 관리` with its search and selection', async () => {
    const t = setup()
    await mountPanel(t, 'files')
    await type('git-changes-search-input', 'src')
    await click(fileRow('src/b.ts', 'git-row-select'))
    await click(fileRow('src/a.ts', 'git-row-open'))
    expect(tab()).toBe('diff')
    expect(q('git-center-diff')?.dataset.path).toBe('src/a.ts')
    expect(text('git-center-diff-compare')).toBe('작업 기준(v0.2) ↔ 작업 파일')
    expect(t.wt.source.loadFileDiff.mock.calls).toEqual([[P, G, 'src/a.ts', null]])
    await click('git-center-diff-back')
    expect(tab()).toBe('files')
    expect(q('git-center-diff')).toBeNull()
    expect((q('git-changes-search-input') as HTMLInputElement).value).toBe('src')
    expect((fileRow('src/b.ts', 'git-row-select') as HTMLInputElement).checked).toBe(true)
    // The `Diff` tab shows its list again.
    await openTab('diff')
    expect(q('git-center-diff')).toBeNull()
    expect(rows()).toEqual(['src/a.ts', 'src/b.ts', 'lib/c.ts'])
  })

  it('`파일 경로` shows the full path once, `비교 기준` on its own; no file name besides', async () => {
    const path = 'client_gui/src/features/git/a/very/deep/folder/LongFileName.vue'
    const t = setup({ targetChanges: { 'v0.2': list('v0.2', [path], 'A') } })
    t.wt.db.fileDiffs[path] = fileDiff(path, { status: 'A' })
    await mountPanel(t)
    await click(row(path))
    const labels = qa('.gw-diff-meta-row dt').map((d) => d.textContent)
    expect(labels).toEqual(['파일 경로', '비교 기준'])
    expect(text('git-center-diff-path')).toBe(path)
    expect(text('git-center-diff-letter')).toBe('A')
    expect(text('git-center-diff-compare')).toBe('v0.2 기준 ↔ 작업 파일')
    // Everything above the code (whose lines are the file's own text).
    const detail = [...q('git-center-diff')!.children].filter((c) => !c.matches('.gw-diff-code')).map((c) => c.textContent).join(' ')
    expect(detail.split('LongFileName.vue').length - 1).toBe(1)
    // The window title keeps the worktree branch; the path is not repeated there either.
    expect(q('git-window-title')?.textContent).not.toContain('LongFileName.vue')
  })

  it('no difference, a failed read and a network failure are three different lines', async () => {
    const t = setup()
    t.wt.db.fileDiffs['src/a.ts'] = fileDiff('src/a.ts', {
      old: { exists: true, binary: false, truncated: false, size: 2, content: 'x\n' },
      new: { exists: true, binary: false, truncated: false, size: 2, content: 'x\n' },
    })
    await mountPanel(t)
    await click(row('src/a.ts'))
    expect(text('git-center-diff-same')).toBe('차이 없음')
    expect(q('git-center-diff-error')).toBeNull()
    expect(q('git-center-diff-lines')).toBeNull()
    await click('git-center-diff-back')

    t.wt.source.loadFileDiff.mockRejectedValueOnce(Object.assign(new Error('x'), { cause: gitError(500, 'git_error') }))
    await click(row('src/b.ts'))
    expect(q('git-center-diff-same')).toBeNull()
    expect(q('git-center-diff-error')?.dataset.kind).toBe('failed')
    expect(text('git-center-diff-error')).toBe('Diff를 읽지 못했습니다. (git_error)')
    expect(q('git-center-diff-retry')).toBeNull()
    await click('git-center-diff-back')

    t.wt.source.loadFileDiff.mockRejectedValueOnce(Object.assign(new Error('x'), { cause: networkError() }))
    await click(row('lib/c.ts'))
    expect(q('git-center-diff-error')?.dataset.kind).toBe('network')
    await click('git-center-diff-retry')
    expect(q('git-center-diff-error')).toBeNull()
    expect(qa('.gw-diff-line').map((l) => l.dataset.kind)).toEqual(['del', 'add', 'context'])
  })

  it('a worktree change reads the open file again; a target change closes a list file, not a `파일 관리` one', async () => {
    const t = setup({ targetChanges: { 'v0.2': list('v0.2', ['src/a.ts']), release: list('release', ['r.ts']) } })
    await mountPanel(t, 'files')
    // `src/` is not the first folder: a search opens it.
    await type('git-changes-search-input', 'src')
    await click(fileRow('src/a.ts', 'git-row-open'))
    expect(t.wt.source.loadFileDiff).toHaveBeenCalledTimes(1)
    // Stage another file from `파일 관리`: the status fingerprint changes and the open diff reads again.
    await openTab('files')
    await click(fileRow('src/b.ts', 'git-row-stage'))
    expect(t.wt.source.stage).toHaveBeenCalledTimes(1)
    expect(t.wt.source.loadFileDiff.mock.calls).toEqual([
      [P, G, 'src/a.ts', null],
      [P, G, 'src/a.ts', null],
    ])
    // A target change leaves the `파일 관리` file open.
    await openTab('merge')
    const select = q('git-apply-target') as HTMLSelectElement
    select.value = 'release'
    select.dispatchEvent(new Event('change'))
    await flushPromises()
    await openTab('diff')
    expect(q('git-center-diff')?.dataset.path).toBe('src/a.ts')
    // A list file of `release` closes when the target goes back to v0.2; the list is there.
    await click('git-center-diff-back')
    await openTab('diff')
    await click(row('r.ts'))
    expect(q('git-center-diff')?.dataset.path).toBe('r.ts')
    await openTab('merge')
    select.value = 'v0.2'
    select.dispatchEvent(new Event('change'))
    await flushPromises()
    await openTab('diff')
    expect(q('git-center-diff')).toBeNull()
    expect(rows()).toEqual(['src/a.ts'])
  })

  it('closing the window closes the file; opened again on `Diff`, the list comes first', async () => {
    const t = setup()
    await mountPanel(t)
    await click(row('src/b.ts'))
    expect(q('git-center-diff')).not.toBeNull()
    const shell = q('git-window')!.closest<HTMLElement>('.ui-modeless-panel')!
    await click(shell.querySelector<HTMLElement>('button[aria-label="Close"]'))
    expect(q('git-window')).toBeNull()
    await click(document.body.querySelector<HTMLElement>('[data-test="git-worktree-entry"][data-tab="diff"]'))
    expect(q('git-center-diff')).toBeNull()
    expect(rows()).toEqual(['src/a.ts', 'src/b.ts', 'lib/c.ts'])
  })

  it('another Flow closes the window and the file; nothing is raised for the Designer Center', async () => {
    const t = setup()
    const w = await mountPanel(t)
    await click(row('src/a.ts'))
    await w.setProps({ flow: { groupId: 'p.m.0002', name: '다른 Flow' } })
    await flushPromises()
    expect(q('git-window')).toBeNull()
    expect(q('git-center-diff')).toBeNull()
    expect(w.emitted('open-diff')).toBeUndefined()
    expect(w.emitted('git-center-diff-refresh')).toBeUndefined()
  })
})

describe('`Diff` tab — reads that answer late (R §6-1, §3-4)', () => {
  it('opened before the first E8 answer: reading, then the default target list once it comes', async () => {
    const t = setup()
    // Every E8 of the opening (gate opening and window opening) answers only when released.
    const e8 = deferred<ManualMergeView>()
    t.wt.source.loadManualMerge.mockImplementation(() => e8.promise)
    await mountPanel(t)
    expect(tab()).toBe('diff')
    // No target is known yet: not 머지 대상 없음, the list is on its way.
    expect(q('git-apply-diff-none')).toBeNull()
    expect(text('git-apply-diff-loading')).toBe('읽는 중…')
    expect(t.wt.source.loadTargetChanges).not.toHaveBeenCalled()
    e8.resolve(manualMergeView())
    await flushPromises()
    expect(t.wt.source.loadTargetChanges.mock.calls).toEqual([[P, G, 'v0.2']])
    expect(q('git-apply-diff-loading')).toBeNull()
    expect(text('git-apply-diff-title')).toBe('v0.2 기준 변경 3개')
    expect(rows()).toEqual(['src/a.ts', 'src/b.ts', 'lib/c.ts'])
  })

  it('an E8 answer with no eligible default target says 머지 대상 없음 and reads no list', async () => {
    const t = setup()
    t.wt.db.merge = manualMergeView({
      targets: [{ name: 'v0.2', kind: 'base', eligible: false, blockedReason: 'blocked', isWorkBase: true }],
    })
    await mountPanel(t)
    expect(text('git-apply-diff-none')).toBe('머지 대상 없음')
    expect(q('git-apply-diff-loading')).toBeNull()
    expect(t.wt.source.loadTargetChanges).not.toHaveBeenCalled()
  })

  it('the gate closing (git/status failed) closes the file; recovered, `Diff` starts on the list and a late answer stays out', async () => {
    const t = setup()
    await mountPanel(t)
    const late = deferred<FlowFileDiff>()
    t.wt.source.loadFileDiff.mockImplementationOnce(() => late.promise)
    await click(row('src/a.ts'))
    expect(q('git-center-diff')?.dataset.path).toBe('src/a.ts')
    // git/status fails: the gate closes and the worktree area with its window goes away.
    t.fake.source.loadStatus.mockRejectedValueOnce(gitError(500, 'git_error'))
    await click('git-refresh')
    expect(q('git-worktree')).toBeNull()
    expect(q('git-window')).toBeNull()
    // The same project and Flow read again: the gate opens on the same context.
    await click('git-refresh')
    expect(q('git-worktree')).not.toBeNull()
    await click(document.body.querySelector<HTMLElement>('[data-test="git-worktree-entry"][data-tab="diff"]'))
    expect(tab()).toBe('diff')
    expect(q('git-center-diff')).toBeNull()
    expect(rows()).toEqual(['src/a.ts', 'src/b.ts', 'lib/c.ts'])
    // The diff asked before the gate closed answers now: it opens nothing.
    late.resolve(fileDiff('src/a.ts'))
    await flushPromises()
    expect(q('git-center-diff')).toBeNull()
    expect(shown(q('git-apply-info'))).toBe(true)
    // A row opens its own read as usual.
    await click(row('src/b.ts'))
    expect(q('git-center-diff')?.dataset.path).toBe('src/b.ts')
    expect(t.wt.source.loadFileDiff.mock.calls).toEqual([
      [P, G, 'src/a.ts', 'v0.2'],
      [P, G, 'src/b.ts', 'v0.2'],
    ])
    expect(qa('.gw-diff-text').map((l) => l.textContent)).toContain('new src/b.ts')
  })
})

describe('diffListPage (pure)', () => {
  const entries = (n: number) => many(n).map((path) => ({ path }))

  it('pages of 16 in list order, the page kept inside the pages there are', () => {
    expect(WORKTREE_DIFF_PAGE).toBe(16)
    const first = diffListPage(entries(40), '', 0)
    expect([first.rows.length, first.matched, first.page, first.pages, first.from, first.to]).toEqual([16, 40, 0, 3, 1, 16])
    const last = diffListPage(entries(40), '', 9)
    expect([last.rows.length, last.page, last.from, last.to]).toEqual([8, 2, 33, 40])
    expect(last.rows[0].path).toBe('src/f32.ts')
    expect(diffListPage(entries(40), '', -3).page).toBe(0)
  })

  it('a search matches part of the path in any case; nothing matched is page 1 of 1 with 0–0', () => {
    const changes = [{ path: 'src/App.vue' }, { path: 'lib/app.ts' }, { path: 'docs/readme.md' }]
    expect(diffListPage(changes, '  APP ', 0).rows.map((r) => r.path)).toEqual(['src/App.vue', 'lib/app.ts'])
    const none = diffListPage(changes, 'zzz', 4)
    expect([none.rows.length, none.matched, none.page, none.pages, none.from, none.to]).toEqual([0, 0, 0, 1, 0, 0])
    expect(diffListPage([], '', 0).pages).toBe(1)
  })
})

describe('the code area keeps its columns (R §6-4, CSS)', () => {
  it('the list and the detail take the tab height; the code scrolls both ways inside itself', () => {
    expect(rule('.gw-diff-list, .gw-diff')).toMatch(/flex:\s*1 1 0/)
    expect(rule('.gw-diff-list, .gw-diff')).toMatch(/min-height:\s*0/)
    const code = rule('.gw-diff-code')
    expect(code).toMatch(/overflow:\s*auto/)
    expect(code).toMatch(/min-width:\s*0/)
    expect(code).toMatch(/flex:\s*1 1 0/)
    const line = rule('.gw-diff-line')
    expect(line).toMatch(/width:\s*max-content/)
    expect(line).toMatch(/min-width:\s*100%/)
    expect(line).toMatch(/white-space:\s*pre/)
    // Fixed number and sign columns never shrink under a long line.
    expect(rule('.gw-diff-no')).toMatch(/flex:\s*0 0 42px/)
    expect(rule('.gw-diff-sign')).toMatch(/flex:\s*0 0 16px/)
    expect(rule('.gw-diff-text')).toMatch(/flex:\s*none/)
    // The path wraps rather than being cut, so it is shown whole once.
    expect(rule('.gw-diff-path')).toMatch(/overflow-wrap:\s*anywhere/)
  })
})
