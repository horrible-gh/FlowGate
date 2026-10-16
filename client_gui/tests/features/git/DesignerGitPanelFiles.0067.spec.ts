import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import type { WorktreeFile } from '@/features/git/types'
import { buildSections, folderSelection, isSelectable, type WorktreeListOptions } from '@/features/git/worktreeList'
import { G, P, createFakeGitSource } from './fakeGitSource'
import { createFakeWorktreeSource, worktreeFile, worktreeStatus, type FakeWorktreeState } from './fakeWorktreeSource'

// flowgate.v02.0067 T#2 — the `파일 관리` tab of the Git modeless (0067-R §4, v27 `modalFilesHTML`):
// 100+ files inside the window, folder checkboxes of three states apart from the fold, protected
// files outside every selection, the selection bar order, and the commit line at the bottom that
// closes with no Stage. Every Stage / commit is still the 0066 session's request to the fake server.
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

function setup(wt: Partial<FakeWorktreeState> = {}) {
  const fakeWt = createFakeWorktreeSource(wt)
  const fake = createFakeGitSource({}, { worktree: fakeWt.source })
  return { wt: fakeWt, fake }
}

async function openFilesTab(t: ReturnType<typeof setup>, props: Record<string, unknown> = {}) {
  wrapper = mount(DesignerGitPanel, {
    props: { projectId: P, flow: FLOW, source: t.fake.source, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  document.body.querySelector<HTMLElement>('[data-test="git-worktree-entry"][data-tab="files"]')!.click()
  await flushPromises()
  return wrapper
}

const q = (sel: string) => document.body.querySelector<HTMLElement>(`[data-test="${sel}"]`)
const qa = (sel: string) => [...document.body.querySelectorAll<HTMLElement>(sel)]
const text = (sel: string) => q(sel)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
const filesPanel = () => document.body.querySelector<HTMLElement>('[data-test="git-window-panel"][data-tab="files"]')!
const folderEl = (folder: string, section = 'changes') =>
  document.body.querySelector<HTMLElement>(`[data-test="git-section-${section}"] [data-test="git-folder"][data-folder="${folder}"]`)!
const folderBox = (folder: string, section = 'changes') =>
  folderEl(folder, section).querySelector<HTMLInputElement>('[data-test="git-folder-select"]')!
const rowBox = (path: string, section = 'changes') =>
  document.body.querySelector<HTMLInputElement>(`[data-test="git-row"][data-path="${path}"][data-section="${section}"] [data-test="git-row-select"]`)
const bulk = () => [...q('git-changes-bulk')!.children].map((c) => (c as HTMLElement).dataset.test)
const css = () => readFileSync(join(resolve(__dirname, '../../..'), 'src/features/git/gitPanel.css'), 'utf8')
const rule = (selector: string) => css().match(new RegExp(`${selector.replace(/[.]/g, '\\.')}\\s*\\{([^}]*)\\}`))?.[1] ?? ''

async function click(target: string | HTMLElement | null) {
  const el = typeof target === 'string' ? q(target) : target
  if (!el) throw new Error(`missing ${String(target)}`)
  el.click()
  await flushPromises()
}

async function typeInto(sel: string, value: string) {
  const el = q(sel) as HTMLInputElement
  el.value = value
  el.dispatchEvent(new Event('input'))
  await flushPromises()
}

const staged = (path: string, overrides: Partial<WorktreeFile> = {}) =>
  worktreeFile(path, { staged: 'M', unstaged: null, canStage: false, canUnstage: true, ...overrides })
const managed = (path: string) =>
  worktreeFile(path, { fileClass: 'managed', protectedReason: 'managed', canStage: false, canRevert: false })

/** `folders` × `per` files under pkg/fNN; the first file of each folder is a FlowGate-managed one. */
function many(folders: number, per: number): WorktreeFile[] {
  const files: WorktreeFile[] = []
  for (let f = 0; f < folders; f++) {
    for (let i = 0; i < per; i++) {
      const path = `pkg/f${String(f).padStart(2, '0')}/file_${String(i).padStart(3, '0')}.ts`
      files.push(i === 0 ? managed(path) : worktreeFile(path))
    }
  }
  return files
}

describe('파일 관리 — 100+ files stay inside the window (0067-R §4-1)', () => {
  it('150 files: one page of the first folder, more, fold per folder, search and type filter — all in the viewport above the commit line', async () => {
    const t = setup({ status: worktreeStatus({ files: many(6, 25) }) })
    await openFilesTab(t)
    expect([text('git-changes-count'), text('git-changes-staged')]).toEqual(['150', '0'])
    const viewport = q('git-changes-viewport')!
    expect(filesPanel().contains(viewport)).toBe(true)
    expect(qa('[data-test="git-folder"]')).toHaveLength(6)
    expect(qa('[data-test="git-folder"]').map((f) => f.dataset.open)).toEqual(['true', 'false', 'false', 'false', 'false', 'false'])
    expect(qa('[data-test="git-row"]')).toHaveLength(10)
    expect(qa('[data-test="git-row"]').every((r) => viewport.contains(r))).toBe(true)
    expect(text('git-folder-more')).toBe('15개 더 있음 · 10개씩 더 보기')
    await click('git-folder-more')
    expect(qa('[data-test="git-row"]')).toHaveLength(20)
    // The fold button of another folder opens it; nothing gets selected by it.
    await click(folderEl('pkg/f01').querySelector<HTMLElement>('[data-test="git-folder-toggle"]'))
    expect(folderEl('pkg/f01').dataset.open).toBe('true')
    expect(qa('[data-test="git-row"]')).toHaveLength(30)
    expect(q('git-selected-count')).toBeNull()
    // Search (always there) opens every folder it matches; the filter narrows by change type.
    await typeInto('git-changes-search-input', 'file_007')
    expect(qa('[data-test="git-row"]')).toHaveLength(6)
    expect(text('git-section-changes-count')).toBe('6 / 150')
    await typeInto('git-changes-search-input', '')
    const filter = q('git-changes-filter') as HTMLSelectElement
    filter.value = 'locked'
    filter.dispatchEvent(new Event('change'))
    await flushPromises()
    expect(text('git-section-changes-count')).toBe('6 / 150')
    // The commit line is below the viewport, never inside it.
    const bar = q('git-commit-bar')!
    expect(viewport.contains(bar)).toBe(false)
    expect(viewport.compareDocumentPosition(bar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(filesPanel().lastElementChild).toBe(bar)
  })

  it('CSS: the tab body fills the window, the viewport takes the rest and scrolls, the commit line keeps its height', () => {
    expect(rule('.gw-files')).toMatch(/flex:\s*1 1 0/)
    expect(rule('.gw-viewport')).toMatch(/flex:\s*1 1 0/)
    expect(rule('.gw-viewport')).toMatch(/min-height:\s*88px/)
    expect(rule('.gw-viewport')).toMatch(/overflow:\s*auto/)
    expect(rule('.gw-commit')).toMatch(/flex:\s*none/)
    expect(rule('.gw-win-body')).toMatch(/overflow:\s*auto/)
  })
})

describe('파일 관리 — folder selection (0067-R §4-2, §4-3)', () => {
  it('the folder checkbox is apart from the fold and shows all / some / none of its selectable files', async () => {
    const t = setup({ status: worktreeStatus({ files: many(6, 25) }) })
    await openFilesTab(t)
    const head = folderEl('pkg/f00').querySelector('.gw-folder-head')!
    // Two controls: the checkbox, then the fold button (the checkbox is not inside the button).
    expect([...head.children].map((c) => (c as HTMLElement).dataset.test)).toEqual(['git-folder-select', 'git-folder-toggle'])
    expect(folderBox('pkg/f00').getAttribute('aria-label')).toBe('pkg/f00 폴더 파일 선택')
    expect([folderBox('pkg/f00').checked, folderBox('pkg/f00').indeterminate]).toEqual([false, false])

    await click(rowBox('pkg/f00/file_001.ts'))
    expect([folderBox('pkg/f00').checked, folderBox('pkg/f00').indeterminate, folderBox('pkg/f00').dataset.state]).toEqual([false, true, 'some'])

    // Mixed → a press checks every selectable file of the folder, past the display limit too.
    await click(folderBox('pkg/f00'))
    expect([folderBox('pkg/f00').checked, folderBox('pkg/f00').indeterminate, folderBox('pkg/f00').dataset.state]).toEqual([true, false, 'all'])
    expect(text('git-selected-count')).toBe('선택 24개')
    expect(folderEl('pkg/f00').dataset.open).toBe('true')

    // Folding keeps the selection and its state.
    await click(folderEl('pkg/f00').querySelector<HTMLElement>('[data-test="git-folder-toggle"]'))
    expect(folderEl('pkg/f00').dataset.open).toBe('false')
    expect(folderBox('pkg/f00').checked).toBe(true)
    expect(text('git-selected-count')).toBe('선택 24개')

    // All → a press clears the folder.
    await click(folderBox('pkg/f00'))
    expect([folderBox('pkg/f00').checked, folderBox('pkg/f00').indeterminate]).toEqual([false, false])
    expect(q('git-selected-count')).toBeNull()
  })

  it('a folded folder selects its files; Stage sends exactly them — never the managed one', async () => {
    const t = setup({ status: worktreeStatus({ files: many(6, 25) }) })
    await openFilesTab(t)
    expect(folderEl('pkg/f02').dataset.open).toBe('false')
    await click(folderBox('pkg/f02'))
    expect(text('git-selected-count')).toBe('선택 24개')
    await click('git-stage-selected')
    expect(t.wt.source.stage).toHaveBeenCalledTimes(1)
    const [gid, selection] = t.wt.source.stage.mock.calls[0]
    expect(gid).toBe(G)
    const paths = (selection as { paths: string[] }).paths
    expect(paths).toHaveLength(24)
    expect(paths.every((p) => p.startsWith('pkg/f02/'))).toBe(true)
    expect(paths).not.toContain('pkg/f02/file_000.ts')
    // The answer applies: those 24 are staged now and the selection is gone.
    expect(text('git-changes-staged')).toBe('24')
    expect(q('git-selected-count')).toBeNull()
  })

  it('under a search the folder checkbox selects what the search shows of it', async () => {
    const t = setup({ status: worktreeStatus({ files: many(3, 25) }) })
    await openFilesTab(t)
    await typeInto('git-changes-search-input', 'file_01')
    await click(folderBox('pkg/f01'))
    expect(text('git-selected-count')).toBe('선택 10개')
    await typeInto('git-changes-search-input', '')
    expect(folderBox('pkg/f01').indeterminate).toBe(true)
  })

  it('protected files: no checkbox, not in 전체 선택 or a folder; a folder of only protected files has its checkbox off', async () => {
    const t = setup({
      status: worktreeStatus({
        files: [worktreeFile('src/a.ts'), managed('src/lock.ts'), managed('flowgate/m1.json'), managed('flowgate/m2.json')],
      }),
    })
    await openFilesTab(t)
    // `flowgate` is the first folder (open); `src` opens with its fold button.
    expect(folderBox('flowgate').disabled).toBe(true)
    await click(folderEl('src').querySelector<HTMLElement>('[data-test="git-folder-toggle"]'))
    expect(rowBox('src/lock.ts')!.disabled).toBe(true)
    expect(rowBox('src/a.ts')!.disabled).toBe(false)
    await click(folderBox('src'))
    expect(text('git-selected-count')).toBe('선택 1개')
    expect(folderBox('src').checked).toBe(true)
    await click('git-select-all')
    expect(text('git-select-all')).toBe('전체 선택')
    expect(q('git-selected-count')).toBeNull()
    await click('git-select-all')
    expect(text('git-selected-count')).toBe('선택 1개')
    // 전체 Stage is the server's whole-group request; it skips the protected files itself.
    await click('git-changes-stage-all')
    expect(t.wt.source.stage).toHaveBeenCalledWith(G, { all: true }, 'wt-1')
    expect(text('git-changes-notice')).toContain('보호·불가 항목 3개는 건너뜀')
    expect(text('git-changes-staged')).toBe('1')
  })
})

describe('파일 관리 — selection bar (0067-R §4-4)', () => {
  it('left to right: 전체 선택 → Stage → Stage 해제 → 전체 Stage / 전체 해제, only the active ones, the count aside', async () => {
    const t = setup({ status: worktreeStatus({ files: [worktreeFile('src/a.ts'), worktreeFile('src/b.ts'), staged('src/s.ts')] }) })
    await openFilesTab(t)
    expect(bulk()).toEqual(['git-select-all', 'git-changes-stage-all'])
    expect(text('git-select-all')).toBe('전체 선택')
    expect(text('git-changes-stage-all')).toBe('전체 Stage')
    await click(rowBox('src/a.ts'))
    expect(bulk()).toEqual(['git-select-all', 'git-stage-selected', 'git-changes-stage-all', 'git-selected-count'])
    await click(rowBox('src/s.ts', 'staged'))
    expect(bulk()).toEqual(['git-select-all', 'git-stage-selected', 'git-unstage-selected', 'git-changes-stage-all', 'git-selected-count'])
    expect([text('git-stage-selected'), text('git-unstage-selected'), text('git-selected-count')]).toEqual(['Stage', 'Stage 해제', '선택 2개'])
    // One colour per action (v27 gw-bulk-reset / -stage / -unstage / -all).
    expect(['git-select-all', 'git-stage-selected', 'git-unstage-selected', 'git-changes-stage-all'].map((s) => q(s)!.className)).toEqual([
      'gw-act gw-bulk-reset',
      'gw-act gw-bulk-stage',
      'gw-act gw-bulk-unstage',
      'gw-act gw-bulk-all',
    ])
    for (const sel of ['.gw-bulk-stage', '.gw-bulk-unstage', '.gw-bulk-all']) expect(rule(sel), sel).toMatch(/color:/)
    // Stage 해제 sends only the staged one of the selection.
    await click('git-unstage-selected')
    expect(t.wt.source.unstage).toHaveBeenCalledWith(G, { paths: ['src/s.ts'] }, 'wt-1')
  })

  it('nothing left to stage: 전체 해제 takes the last place', async () => {
    const t = setup({ status: worktreeStatus({ files: [staged('src/s.ts'), staged('src/t.ts')] }) })
    await openFilesTab(t)
    expect(bulk()).toEqual(['git-select-all', 'git-changes-unstage-all'])
    expect(text('git-changes-unstage-all')).toBe('전체 해제')
    await click('git-changes-unstage-all')
    expect(t.wt.source.unstage).toHaveBeenCalledWith(G, { all: true }, 'wt-1')
  })
})

describe('파일 관리 — commit line (0067-R §4-5)', () => {
  const message = () => q('git-commit-message') as HTMLInputElement
  const ai = () => q('git-commit-ai') as HTMLButtonElement
  const commit = () => q('git-commit') as HTMLButtonElement

  it('Stage 0: message, 🤖 and 커밋 all disabled, with no line just for that; a Stage opens message and 🤖; an empty message keeps 커밋 off', async () => {
    const t = setup({ status: worktreeStatus({ files: [worktreeFile('src/a.ts')] }) })
    await openFilesTab(t)
    expect([message().disabled, ai().disabled, commit().disabled]).toEqual([true, true, true])
    expect(q('git-commit-reason')).toBeNull()
    expect(text('git-commit-bar')).not.toMatch(/Stage된 파일 없음/)
    expect(filesPanel().textContent).not.toMatch(/Stage된 파일 없음/)

    await click(document.body.querySelector<HTMLElement>('[data-test="git-row"][data-path="src/a.ts"] [data-test="git-row-stage"]'))
    expect(text('git-changes-staged')).toBe('1')
    expect([message().disabled, ai().disabled, commit().disabled]).toEqual([false, false, true])
    await typeInto('git-commit-message', '   ')
    expect(commit().disabled).toBe(true)
    await typeInto('git-commit-message', 'feat: files')
    expect(commit().disabled).toBe(false)
    expect(t.wt.source.commit).not.toHaveBeenCalled()
  })

  it('not operable (read-only): all three disabled even with a Stage', async () => {
    const t = setup({ status: worktreeStatus({ files: [staged('src/s.ts')] }) })
    await openFilesTab(t, { readOnly: true })
    expect([message().disabled, ai().disabled, commit().disabled]).toEqual([true, true, true])
    expect(text('git-commit-reason')).toBe('읽기 전용')
  })

  it('a blocker on the commit closes the message and 🤖 too (no suggest request); one on Stage only leaves both open', async () => {
    // Stage 1, a default AI: the shared reasons keep 🤖 itself enabled, the commit area closes it.
    const blocked = setup({
      status: worktreeStatus({ files: [staged('src/s.ts')], blockers: [{ code: 'run_already_active', operations: ['commit'] }] }),
    })
    await openFilesTab(blocked)
    expect([message().disabled, ai().disabled, commit().disabled]).toEqual([true, true, true])
    expect([message().title, ai().title, commit().title]).toEqual(Array(3).fill('AI 실행 중 · Git 변경 잠금'))
    expect(text('git-commit-reason')).toBe('AI 실행 중 · Git 변경 잠금')
    // The handler refuses on its own as well: a click past the disabled attribute sends nothing.
    ai().disabled = false
    await click(ai())
    expect(blocked.wt.source.suggestCommitMessage).not.toHaveBeenCalled()
    wrapper?.unmount()
    document.body.innerHTML = ''

    // A Stage-only blocker with an empty message: message and 🤖 open, 커밋 waits for the message.
    const stageOnly = setup({
      status: worktreeStatus({ files: [staged('src/s.ts')], blockers: [{ code: 'run_already_active', operations: ['stage'] }] }),
    })
    await openFilesTab(stageOnly)
    expect([message().disabled, ai().disabled, commit().disabled]).toEqual([false, false, true])
    expect(ai().title).toBe('AI 커밋 메시지 초안 — 커밋하지 않음')
    await click(ai())
    expect(stageOnly.wt.source.suggestCommitMessage).toHaveBeenCalledTimes(1)
  })
})

describe('worktreeList — folder selection (pure)', () => {
  const options = (overrides: Partial<WorktreeListOptions> = {}): WorktreeListOptions => ({
    search: '',
    filter: 'all',
    folderMode: 'default',
    folderToggles: {},
    limits: {},
    ...overrides,
  })

  it('a folder lists every matching selectable path, folded or past the limit; managed / protected never', () => {
    const status = worktreeStatus({ files: many(2, 25) })
    const [changes] = buildSections(status, options())
    expect(changes.folders.map((f) => [f.folder, f.open, f.rows.length, f.selectable.length])).toEqual([
      ['pkg/f00', true, 10, 24],
      ['pkg/f01', false, 0, 24],
    ])
    expect(changes.folders[0].selectable).not.toContain('pkg/f00/file_000.ts')
    const [searched] = buildSections(status, options({ search: 'file_01' }))
    expect(searched.folders.map((f) => f.selectable.length)).toEqual([10, 10])
    expect(isSelectable(managed('m.ts'))).toBe(false)
    expect(isSelectable(staged('mg.ts', { fileClass: 'managed', protectedReason: 'managed' }))).toBe(false)
    expect(isSelectable(staged('s.ts'))).toBe(true)
  })

  it('folderSelection: all / some / none; an empty folder is none', () => {
    expect(folderSelection(['a', 'b'], ['a', 'b', 'c'])).toBe('all')
    expect(folderSelection(['a', 'b'], ['b'])).toBe('some')
    expect(folderSelection(['a', 'b'], ['c'])).toBe('none')
    expect(folderSelection([], ['a'])).toBe('none')
  })
})
