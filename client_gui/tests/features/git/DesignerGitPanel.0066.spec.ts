import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import type { ProjectGitStatus } from '@/features/project'
import { DesignerGitPanel } from '@/features/git'
import type { WorktreeCounts, WorktreeFile, WorktreeStatus } from '@/features/git/types'
import { G, P, createFakeGitSource, gitError, row, status } from './fakeGitSource'
import { createFakeWorktreeSource, worktreeFile, worktreeStatus } from './fakeWorktreeSource'

// flowgate.v02.0066 T#6 — the worktree area of the Git tab mounted with in-memory sources:
// gate, changed-files accordion, selection, full Stage / unstage with hidden entries, commit, AI
// draft, revert / remove, badge, read-only / 403 and the worktree `open-diff` intent
// (0066 D#2 §3-1, §3-3, §3-5, §3-6, §3-8, §6-1–§6-3; C5–C13, C24, C25), and confirm dialogs /
// late answers bound to the Flow they were made in (§3-2-2 ①, C23).
// flowgate.v02.0067 T#4 — a row name opens its diff in the Git modeless `Diff` tab (no `open-diff`,
// no `git-center-diff-refresh` any more); DesignerGitPanelDiff.0067 has the tab itself.
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

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => (resolve = res))
  return { promise, resolve }
}

const counts = (overrides: Partial<WorktreeCounts>): WorktreeCounts => ({
  changed: 0,
  staged: 0,
  unstaged: 0,
  untracked: 0,
  excludedNewUntracked: 0,
  hidden: 0,
  managedDirty: 0,
  unsupported: 0,
  ...overrides,
})
const staged = (path: string, overrides: Partial<WorktreeFile> = {}) =>
  worktreeFile(path, { staged: 'M', unstaged: null, canStage: false, canUnstage: true, ...overrides })
const added = (path: string) =>
  worktreeFile(path, { inHead: false, unstaged: null, untracked: true, canRevert: false, canRemove: true })

function setup(wtStatus?: WorktreeStatus, gitStatus?: ProjectGitStatus | null) {
  const wt = createFakeWorktreeSource(wtStatus ? { status: wtStatus } : {})
  const fake = createFakeGitSource(gitStatus === undefined ? {} : { status: gitStatus }, { worktree: wt.source })
  return { wt, fake }
}

// flowgate.v02.0067 T#1 — the changed files and the commit line live in the Git modeless the
// `파일 관리` entry opens; the panel is mounted with it open there unless `window: false`.
// flowgate.v02.0067 T#2 — that tab has no fold of its own: the list, search / filter and the
// selection bar are there as soon as it opens.
const entry = (tab: string) => document.body.querySelector<HTMLElement>(`[data-test="git-worktree-entry"][data-tab="${tab}"]`)

async function mountPanel(t: ReturnType<typeof setup>, props: Record<string, unknown> = {}, opts: { window?: boolean } = {}) {
  wrapper = mount(DesignerGitPanel, {
    props: { projectId: P, flow: FLOW, source: t.fake.source, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  if (opts.window !== false) await openFiles()
  return wrapper
}

async function openFiles() {
  const button = entry('files')
  if (!button) return
  button.click()
  await flushPromises()
}

const q = (sel: string) => document.body.querySelector<HTMLElement>(`[data-test="${sel}"]`)
const qa = (sel: string) => [...document.body.querySelectorAll<HTMLElement>(sel)]
const text = (sel: string) => q(sel)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
const rowEl = (path: string, section = 'changes') =>
  document.body.querySelector<HTMLElement>(`[data-test="git-row"][data-path="${path}"][data-section="${section}"]`)
const inRow = (path: string, sel: string, section = 'changes') => rowEl(path, section)?.querySelector<HTMLElement>(`[data-test="${sel}"]`) ?? null

async function click(target: string | HTMLElement | null) {
  const el = typeof target === 'string' ? q(target) : target
  if (!el) throw new Error(`missing ${String(target)}`)
  el.click()
  await flushPromises()
}

async function typeInto(sel: string, value: string) {
  const el = q(sel) as HTMLInputElement | null
  if (!el) throw new Error(`missing ${sel}`)
  el.value = value
  el.dispatchEvent(new Event('input'))
  await flushPromises()
}

async function confirmDialog() {
  const button = qa('.ui-confirm-actions button').at(-1)
  if (!button) throw new Error('no confirm dialog')
  button.click()
  await flushPromises()
}

const confirmText = () => qa('.ui-confirm-message').map((e) => e.textContent).join(' ')

/** `folders` × `per` files, every ninth a new file. */
function many(folders: number, per: number): WorktreeFile[] {
  const files: WorktreeFile[] = []
  for (let f = 0; f < folders; f++) {
    for (let i = 0; i < per; i++) {
      const path = `pkg/f${String(f).padStart(2, '0')}/file_${String(i).padStart(3, '0')}.ts`
      files.push(i % 9 === 0 ? added(path) : worktreeFile(path))
    }
  }
  return files
}

describe('worktree area — gate and first look (C5)', () => {
  it('appears below the 0033 area once git/status says Git on: the summary and four entries; the list and commit line in the modeless', async () => {
    const t = setup()
    await mountPanel(t, {}, { window: false })
    expect(q('git-worktree')).not.toBeNull()
    expect(text('git-worktree-summary-changed')).toBe('1')
    expect(text('git-worktree-summary-staged')).toBe('0')
    expect(text('git-worktree-summary-merge')).toBe('1')
    expect(q('git-worktree-chip')).toBeNull()
    expect(q('git-changes')).toBeNull()
    expect(q('git-commit-bar')).toBeNull()
    expect(t.wt.source.loadStatus).toHaveBeenCalledTimes(1)
    expect(t.wt.source.loadManualMerge).toHaveBeenCalledTimes(1)
    expect(t.wt.source.loadMergeResults).toHaveBeenCalledTimes(1)
    await openFiles()
    expect(q('git-changes-viewport')).not.toBeNull()
    expect(rowEl('src/a.ts')).not.toBeNull()
    expect(q('git-commit-bar')).not.toBeNull()
    // flowgate.v02.0067 T#1: opening the Git modeless reads the worktree once more.
    expect(t.wt.source.loadStatus).toHaveBeenCalledTimes(2)
    expect(t.wt.source.loadManualMerge).toHaveBeenCalledTimes(2)
    expect(t.wt.source.loadMergeResults).toHaveBeenCalledTimes(2)
    expect(t.wt.source.loadTargetChanges).not.toHaveBeenCalled()
  })

  it('nothing before the first git/status answer, nothing over Git off or a failed read', async () => {
    const t = setup()
    const answer = deferred<ProjectGitStatus | null>()
    t.fake.source.loadStatus.mockImplementationOnce(() => answer.promise)
    await mountPanel(t)
    expect(q('git-worktree')).toBeNull()
    expect(t.wt.source.loadStatus).not.toHaveBeenCalled()
    answer.resolve(status())
    await flushPromises()
    expect(q('git-worktree')).not.toBeNull()
    wrapper?.unmount()
    document.body.innerHTML = ''

    const off = setup(undefined, status({ enabled: false }))
    await mountPanel(off)
    expect(q('git-unlinked')).not.toBeNull()
    expect(q('git-worktree')).toBeNull()
    expect(off.wt.source.loadStatus).not.toHaveBeenCalled()
    wrapper?.unmount()
    document.body.innerHTML = ''

    const failed = setup()
    failed.fake.source.loadStatus.mockRejectedValue(gitError(500, 'git_error'))
    await mountPanel(failed)
    expect(q('git-status-error')).not.toBeNull()
    expect(q('git-worktree')).toBeNull()
    expect(failed.wt.source.loadStatus).not.toHaveBeenCalled()
  })

  it('header refresh and coming into view read both sessions', async () => {
    const t = setup()
    const w = await mountPanel(t, { active: false }, { window: false })
    await click('git-refresh')
    expect(t.fake.source.loadStatus).toHaveBeenCalledTimes(2)
    expect(t.wt.source.loadStatus).toHaveBeenCalledTimes(2)
    await w.setProps({ active: true })
    await flushPromises()
    expect(t.wt.source.loadStatus).toHaveBeenCalledTimes(3)
    expect(t.wt.source.loadMergeResults).toHaveBeenCalledTimes(3)
  })

  it('no workspace (W2): one line, no file list, no commit line', async () => {
    const t = setup(worktreeStatus({ worktreeAvailable: false, files: [], worktreeToken: null, indexToken: null }))
    await mountPanel(t)
    expect(text('git-worktree-unavailable')).toBe('작업 공간 없음(정리된 그룹)')
    expect(q('git-changes')).toBeNull()
    expect(q('git-commit-bar')).toBeNull()
  })

  it('an E1 read failure shows a retry line; retry reads again', async () => {
    const t = setup()
    t.wt.source.loadStatus.mockRejectedValueOnce(gitError(500, 'git_error'))
    await mountPanel(t)
    expect(text('git-worktree-error')).toContain('작업 공간 상태를 읽지 못했습니다.')
    await click('git-worktree-retry')
    await openFiles()
    expect(q('git-changes')).not.toBeNull()
  })
})

describe('changed-files accordion — 100+ files (C6)', () => {
  it('1000 files: header numbers are E1, first folder open, a page per folder, more, search, filter', async () => {
    const t = setup(worktreeStatus({ files: many(10, 100) }))
    await mountPanel(t)
    expect(text('git-changes-count')).toBe('1000')
    const folders = qa('[data-test="git-folder"]')
    expect(folders).toHaveLength(10)
    expect(folders.map((f) => f.dataset.open)).toEqual(['true', ...Array(9).fill('false')])
    expect(qa('[data-test="git-row"]')).toHaveLength(10)
    expect(text('git-folder-more')).toBe('90개 더 있음 · 10개씩 더 보기')
    await click('git-folder-more')
    expect(qa('[data-test="git-row"]')).toHaveLength(20)
    // Search opens every matching folder; the section count reads "matched / total".
    await typeInto('git-changes-search-input', 'FILE_027')
    expect(qa('[data-test="git-folder"]').every((f) => f.dataset.open === 'true')).toBe(true)
    expect(qa('[data-test="git-row"]')).toHaveLength(10)
    expect(text('git-section-changes-count')).toBe('10 / 1000')
    // Filter: new files only.
    await typeInto('git-changes-search-input', '')
    const select = q('git-changes-filter') as HTMLSelectElement
    select.value = '?'
    select.dispatchEvent(new Event('change'))
    await flushPromises()
    expect(text('git-section-changes-count')).toBe(`${12 * 10} / 1000`)
    expect(qa('[data-test="git-row-letter"]').every((e) => e.textContent === '?')).toBe(true)
    // Back to every type: the whole section again.
    select.value = 'all'
    select.dispatchEvent(new Event('change'))
    await flushPromises()
    expect(text('git-section-changes-count')).toBe('1000')
  })

  it('the list lives in a bounded viewport; the commit line is outside it and stays in the DOM order below', async () => {
    const t = setup(worktreeStatus({ files: many(10, 100) }))
    await mountPanel(t)
    for (const toggle of qa('[data-test="git-folder"][data-open="false"] [data-test="git-folder-toggle"]')) await click(toggle)
    // Opening every folder still draws one page per folder, never 1000 rows.
    expect(qa('[data-test="git-row"]')).toHaveLength(100)
    const viewport = q('git-changes-viewport')!
    const bar = q('git-commit-bar')!
    expect(viewport.classList.contains('gw-viewport')).toBe(true)
    expect(viewport.contains(bar)).toBe(false)
    expect(viewport.compareDocumentPosition(bar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const css = readFileSync(join(resolve(__dirname, '../../..'), 'src/features/git/gitPanel.css'), 'utf8')
    const rule = css.match(/\.gw-viewport\s*\{([^}]*)\}/)?.[1] ?? ''
    // flowgate.v02.0067 T#2: it takes the rest of the `파일 관리` tab and scrolls inside it.
    expect(rule).toMatch(/flex:\s*1 1 0/)
    expect(rule).toMatch(/overflow:\s*auto/)
  })
})

describe('full Stage and selection (C7, C8)', () => {
  it('C7: + 전체 Stage sends the whole group whatever the search shows, applies the answer, tells what it skipped', async () => {
    const files = [worktreeFile('src/a.ts'), worktreeFile('src/b.ts')]
    const t = setup(worktreeStatus({ files, counts: counts({ changed: 2, unstaged: 2, hidden: 2 }) }))
    await mountPanel(t)
    await typeInto('git-changes-search-input', 'nothing-matches')
    await click('git-changes-stage-all')
    expect(t.wt.source.stage).toHaveBeenCalledTimes(1)
    expect(t.wt.source.stage).toHaveBeenCalledWith(G, { all: true }, 'wt-1')
    expect(text('git-changes-staged')).toBe('2')
    expect(text('git-changes-notice')).toContain('보호·불가 항목 2개는 건너뜀')
  })

  it('C8: selection sends only what each row may do; success clears it, a refusal keeps it and reads again', async () => {
    const t = setup(
      worktreeStatus({
        files: [
          worktreeFile('a.ts'),
          worktreeFile('b.ts'),
          worktreeFile('lock.ts', { fileClass: 'managed', protectedReason: 'managed', canStage: false, canRevert: false }),
          staged('mg.ts', { fileClass: 'managed', protectedReason: 'managed' }),
        ],
      }),
    )
    await mountPanel(t)
    expect((inRow('lock.ts', 'git-row-select') as HTMLInputElement).disabled).toBe(true)
    expect(inRow('lock.ts', 'git-row-stage')).toBeNull()
    expect(text('git-row-tag')).toBe('🔒')
    // flowgate.v02.0067 T#2 (0067-R §4-3): a managed row is never checked, even for its Stage
    // release; its own − (the server's can_unstage) is still there.
    expect((inRow('mg.ts', 'git-row-select', 'staged') as HTMLInputElement).disabled).toBe(true)
    expect(inRow('mg.ts', 'git-row-unstage', 'staged')).not.toBeNull()
    await click(inRow('a.ts', 'git-row-select'))
    expect(text('git-selected-count')).toBe('선택 1개')
    await click('git-stage-selected')
    expect(t.wt.source.stage).toHaveBeenCalledWith(G, { paths: ['a.ts'] }, 'wt-1')
    expect(q('git-selected-count')).toBeNull()

    await click(inRow('b.ts', 'git-row-select'))
    t.wt.source.stage.mockRejectedValueOnce(gitError(409, 'path_protected'))
    const reads = t.wt.source.loadStatus.mock.calls.length
    await click('git-stage-selected')
    expect(text('git-selected-count')).toBe('선택 1개')
    expect(t.wt.source.loadStatus.mock.calls.length).toBe(reads + 1)
    expect(text('git-changes-notice')).toContain('보호 파일이라 처리할 수 없습니다.')
    expect(t.wt.source.stage).toHaveBeenCalledTimes(2)
  })

  it('전체 선택 checks what matches; a second press (선택 해제) clears it', async () => {
    const t = setup(worktreeStatus({ files: [worktreeFile('src/a.ts'), worktreeFile('src/b.ts'), worktreeFile('docs/c.md')] }))
    await mountPanel(t)
    await typeInto('git-changes-search-input', 'src/')
    expect(text('git-select-all')).toBe('전체 선택')
    await click('git-select-all')
    expect(text('git-selected-count')).toBe('선택 2개')
    expect(text('git-select-all')).toBe('선택 해제')
    await click('git-select-all')
    expect(q('git-selected-count')).toBeNull()
  })
})

describe('hidden entries and the full unstage (C9)', () => {
  it('(a) hidden-only Stage: Stage 1, the hidden line and 전체 해제; one E3 { all } releases it', async () => {
    const t = setup(worktreeStatus({ files: [], counts: counts({ staged: 1, hidden: 1 }) }))
    const w = await mountPanel(t)
    expect(q('git-changes-empty')).toBeNull()
    expect(text('git-changes-staged')).toBe('1')
    expect(text('git-changes-hidden-line')).toContain('표시되지 않는 보호 파일 1개')
    expect(text('git-section-hidden-staged')).toContain('표시되지 않는 보호 파일 1개 Stage됨')
    await click('git-changes-unstage-all')
    expect(t.wt.source.unstage).toHaveBeenCalledTimes(1)
    // (e) the request carries no path at all.
    expect(t.wt.source.unstage.mock.calls[0]).toEqual([G, { all: true }, 'wt-1'])
    expect(text('git-changes-notice')).toContain('표시되지 않는 파일 1개 해제')
    expect(w.emitted('open-diff')).toBeUndefined()
  })

  it('(b) listed + hidden Stage: the staged section has the pathless line, its − 전체 해제 releases both', async () => {
    const t = setup(worktreeStatus({ files: [staged('a.ts')], counts: counts({ changed: 1, staged: 2, hidden: 1 }) }))
    await mountPanel(t)
    expect(rowEl('a.ts', 'staged')).not.toBeNull()
    expect(text('git-section-hidden-staged')).toBe('🔒 표시되지 않는 보호 파일 1개 Stage됨 — 커밋 불가')
    expect(q('git-section-hidden-staged')?.querySelector('input,button')).toBeNull()
    await click('git-changes-unstage-all')
    expect(t.wt.source.unstage).toHaveBeenCalledWith(G, { all: true }, 'wt-1')
    expect(text('git-changes-notice')).toContain('표시되지 않는 파일 1개 해제')
    expect(rowEl('a.ts', 'staged')).toBeNull()
  })

  it('(c) Stage counted on listed rows only (staged 0, hidden 1): the hidden line still offers the full unstage', async () => {
    const t = setup(worktreeStatus({ files: [], counts: counts({ hidden: 1 }) }))
    await mountPanel(t)
    expect(q('git-changes-unstage-all')).toBeNull()
    expect(q('git-changes-stage-all')).toBeNull()
    expect(text('git-changes-hidden-line')).toContain('표시되지 않는 보호 파일 1개')
    await click('git-changes-hidden-unstage-all')
    expect(t.wt.source.unstage).toHaveBeenCalledWith(G, { all: true }, 'wt-1')
  })

  it('(d) a hidden Stage disables commit and AI with staged_protected; a server refusal offers the full unstage', async () => {
    const t = setup(worktreeStatus({ files: [staged('a.ts')], counts: counts({ changed: 1, staged: 2, hidden: 1 }) }))
    await mountPanel(t)
    // flowgate.v02.0067 T#2: the commit cannot run, so the message is closed as well.
    expect((q('git-commit-message') as HTMLInputElement).disabled).toBe(true)
    expect((q('git-commit') as HTMLButtonElement).disabled).toBe(true)
    expect((q('git-commit-ai') as HTMLButtonElement).disabled).toBe(true)
    expect(text('git-commit-reason')).toBe('커밋할 수 없는 보호 파일이 Stage됨 — 전체 해제 후 다시')

    wrapper?.unmount()
    document.body.innerHTML = ''
    const plain = setup(worktreeStatus({ files: [staged('a.ts')] }))
    plain.wt.source.commit.mockRejectedValueOnce(gitError(409, 'staged_protected_paths'))
    await mountPanel(plain)
    await typeInto('git-commit-message', 'feat: x')
    await click('git-commit')
    await confirmDialog()
    expect(text('git-commit-notice')).toContain('커밋할 수 없는 보호 파일이 Stage돼 있습니다.')
    expect((q('git-commit-message') as HTMLInputElement).value).toBe('feat: x')
    await click('git-commit-unstage-all')
    expect(plain.wt.source.unstage).toHaveBeenCalledWith(G, { all: true }, 'wt-1')
  })

  it('the count lines: hidden (with the full unstage), tool artifacts, ownership unavailable', async () => {
    const t = setup(
      worktreeStatus({
        files: [worktreeFile('a.ts')],
        counts: counts({ changed: 1, unstaged: 1, hidden: 2, excludedNewUntracked: 5 }),
        ownership: { available: false, reason: 'ownership_read_failed' },
      }),
    )
    await mountPanel(t)
    expect(text('git-changes-hidden-line')).toContain('표시되지 않는 보호 파일 2개(Git 탭에서 Stage·되돌리기 불가)')
    expect(text('git-changes-artifacts-line')).toBe('커밋 대상이 아닌 도구 잔재 5개')
    expect(text('git-changes-ownership-line')).toContain('관리 파일 판정 불가')
  })
})

describe('commit line (C11) and AI draft (C12)', () => {
  it('C11: no Stage → no_staged; blank → no_message; the dialog shows the E1 count and the commit carries its index token', async () => {
    const t = setup()
    await mountPanel(t)
    // flowgate.v02.0067 T#2: no Stage — message, 🤖 and 커밋 all closed, and no line just for it.
    expect((q('git-commit') as HTMLButtonElement).disabled).toBe(true)
    expect((q('git-commit-message') as HTMLInputElement).disabled).toBe(true)
    expect((q('git-commit-ai') as HTMLButtonElement).disabled).toBe(true)
    expect(q('git-commit-reason')).toBeNull()
    expect(q('git-commit')?.getAttribute('title')).toBe('Stage된 파일 없음')
    await click(inRow('src/a.ts', 'git-row-stage'))
    expect(text('git-commit')).toBe('커밋 · 1')
    expect((q('git-commit-message') as HTMLInputElement).disabled).toBe(false)
    expect(text('git-commit-reason')).toBe('커밋 메시지 입력')
    await typeInto('git-commit-message', 'feat: x')
    // Enter never commits.
    q('git-commit-message')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await flushPromises()
    expect(t.wt.source.commit).not.toHaveBeenCalled()
    const indexToken = t.wt.db.status.indexToken
    const merges = t.wt.source.loadManualMerge.mock.calls.length
    await click('git-commit')
    expect(confirmText()).toContain('Stage된 1개 파일을 Group Worktree에 커밋합니다. Base 반영과 Push는 하지 않습니다.')
    await confirmDialog()
    expect(t.wt.source.commit).toHaveBeenCalledWith(G, 'feat: x', indexToken)
    expect((q('git-commit-message') as HTMLInputElement).value).toBe('')
    expect(text('git-commit-notice')).toContain('커밋했습니다(1개 파일)')
    expect(t.wt.source.loadManualMerge.mock.calls.length).toBe(merges + 1)
    expect(t.wt.source.manualMerge).not.toHaveBeenCalled()
    expect(t.wt.source.pushMergeResult).not.toHaveBeenCalled()
    expect(t.fake.source.push).not.toHaveBeenCalled()
    expect(t.fake.source.finalize).not.toHaveBeenCalled()
  })

  it('C11: a refused commit keeps the message', async () => {
    const t = setup(worktreeStatus({ files: [staged('a.ts')] }))
    t.wt.source.commit.mockRejectedValueOnce(gitError(422, 'commit_message_required'))
    await mountPanel(t)
    await typeInto('git-commit-message', 'feat: keep')
    await click('git-commit')
    await confirmDialog()
    expect(text('git-commit-notice')).toContain('커밋 메시지를 입력하세요.')
    expect((q('git-commit-message') as HTMLInputElement).value).toBe('feat: keep')
  })

  it('C12: 🤖 asks E7 with the default AI and fills the line; nothing is committed', async () => {
    const t = setup(worktreeStatus({ files: [staged('a.ts')] }))
    await mountPanel(t)
    await click('git-commit-ai')
    expect(t.wt.source.suggestCommitMessage).toHaveBeenCalledWith(G, 'opus')
    expect((q('git-commit-message') as HTMLInputElement).value).toBe('feat(git): draft subject')
    expect(t.wt.source.commit).not.toHaveBeenCalled()
    // A typed message is replaced only after asking.
    await typeInto('git-commit-message', 'mine')
    await click('git-commit-ai')
    expect(confirmText()).toContain('입력한 메시지를 AI 제안으로 바꿀까요?')
    expect(t.wt.source.suggestCommitMessage).toHaveBeenCalledTimes(1)
    await confirmDialog()
    expect(t.wt.source.suggestCommitMessage).toHaveBeenCalledTimes(2)
  })

  it('C12: a Stage change after the draft is marked; a partial draft is marked; errors are notices', async () => {
    const t = setup(worktreeStatus({ files: [staged('a.ts'), worktreeFile('b.ts')] }))
    await mountPanel(t)
    await click('git-commit-ai')
    expect(q('git-commit-suggestion')).toBeNull()
    await click(inRow('b.ts', 'git-row-stage'))
    expect(text('git-commit-suggestion')).toBe('Stage가 바뀐 뒤의 제안입니다')

    t.wt.source.suggestCommitMessage.mockResolvedValueOnce({
      suggested: 'fix: part',
      providerId: 'opus',
      stagedCount: 2,
      inputTruncated: true,
      head: 'h1',
      indexToken: t.wt.db.status.indexToken,
    })
    await click('git-commit-ai')
    await confirmDialog()
    expect(text('git-commit-suggestion')).toBe('일부 변경만 보고 만든 제안')

    t.wt.source.suggestCommitMessage.mockRejectedValueOnce(gitError(504, 'ai_timeout'))
    await click('git-commit-ai')
    await confirmDialog()
    expect(text('git-commit-notice')).toContain('AI 응답 시간이 초과됐습니다.')
    expect((q('git-commit-message') as HTMLInputElement).value).toBe('fix: part')
  })

  it('C12: no default AI → 🤖 disabled with no_provider', async () => {
    const t = setup(worktreeStatus({ files: [staged('a.ts')] }))
    t.fake.db.providers = { providerIds: ['opus'], defaultProviderId: null }
    await mountPanel(t)
    expect((q('git-commit-ai') as HTMLButtonElement).disabled).toBe(true)
    expect(q('git-commit-ai')?.getAttribute('title')).toBe('기본 AI 미설정')
  })
})

describe('revert / remove (C13)', () => {
  it('an existing row reverts, a new row removes; both ask first, remove as danger; the answer applies', async () => {
    const t = setup(worktreeStatus({ files: [worktreeFile('src/a.ts'), added('src/n.ts')] }))
    const w = await mountPanel(t)
    expect(inRow('src/a.ts', 'git-row-revert')).not.toBeNull()
    expect(inRow('src/a.ts', 'git-row-remove')).toBeNull()
    expect(inRow('src/n.ts', 'git-row-remove')).not.toBeNull()
    expect(inRow('src/n.ts', 'git-row-revert')).toBeNull()

    await click(inRow('src/a.ts', 'git-row-revert'))
    expect(confirmText()).toContain('src/a.ts — HEAD 내용으로 되돌립니다. 작업 내용이 사라집니다.')
    expect(t.wt.source.revert).not.toHaveBeenCalled()
    await confirmDialog()
    expect(t.wt.source.revert).toHaveBeenCalledWith(G, ['src/a.ts'], 'wt-1')
    expect(rowEl('src/a.ts')).toBeNull()
    expect(w.emitted('git-center-diff-refresh')).toBeUndefined()

    await click(inRow('src/n.ts', 'git-row-remove'))
    expect(confirmText()).toContain('src/n.ts — 새 파일을 지웁니다. 되돌릴 수 없습니다.')
    expect(qa('.ui-confirm-actions button').at(-1)?.className).toContain('ui-btn--danger')
    await confirmDialog()
    // The token of the status the revert answered with, not the first one.
    expect(t.wt.source.remove).toHaveBeenCalledWith(G, ['src/n.ts'], expect.stringMatching(/^wt-[2-9]/))
    expect(rowEl('src/n.ts')).toBeNull()
    expect(text('git-changes-empty')).toBe('변경 없음')
    expect(q('git-changes-search')).toBeNull()
    expect(q('git-changes-bulk')).toBeNull()
  })
})

describe('Center Diff intent, badge, read-only / 403 (C14 intent, C24, C25)', () => {
  it('a row name opens its group diff (no compare base) in the `Diff` tab; nothing goes up', async () => {
    const t = setup(worktreeStatus({ files: [worktreeFile('src/a.ts'), added('src/n.ts')] }))
    const w = await mountPanel(t)
    expect(inRow('src/a.ts', 'git-row-open')?.getAttribute('title')).toBe('src/a.ts')
    await click(inRow('src/a.ts', 'git-row-open'))
    expect(q('git-window')?.dataset.tab).toBe('diff')
    expect(q('git-center-diff')?.dataset.path).toBe('src/a.ts')
    expect(text('git-center-diff-letter')).toBe('M')
    await click('git-center-diff-back')
    expect(q('git-window')?.dataset.tab).toBe('files')
    await click(inRow('src/n.ts', 'git-row-open'))
    expect(q('git-center-diff')?.dataset.path).toBe('src/n.ts')
    expect(t.wt.source.loadFileDiff.mock.calls).toEqual([
      [P, G, 'src/a.ts', null],
      [P, G, 'src/n.ts', null],
    ])
    expect(w.emitted('open-diff')).toBeUndefined()
  })

  it('C24: the worktree count joins a count badge only; danger stays; nothing while the area is hidden', async () => {
    const files = [worktreeFile('a.ts'), worktreeFile('b.ts'), worktreeFile('c.ts')]
    const plain = setup(worktreeStatus({ files }))
    let w = await mountPanel(plain)
    expect(w.emitted('badge-change')?.at(-1)).toEqual([{ tone: 'count', text: '3' }])
    wrapper?.unmount()

    const dirty = setup(worktreeStatus({ files }), status({ baseDirty: { files: ['x.ts', 'y.ts'], mergeInProgress: false, aiRun: false } }))
    w = await mountPanel(dirty)
    expect(w.emitted('badge-change')?.at(-1)).toEqual([{ tone: 'count', text: '5' }])
    wrapper?.unmount()

    const danger = setup(worktreeStatus({ files }), status({ pending: [row()] }))
    w = await mountPanel(danger)
    expect(w.emitted('badge-change')?.at(-1)).toEqual([{ tone: 'danger', text: '!' }])
    wrapper?.unmount()

    const off = setup(worktreeStatus({ files }), status({ enabled: false }))
    w = await mountPanel(off)
    expect(w.emitted('badge-change')).toBeUndefined()
  })

  it('C25: read-only disables every write with its reason; reading a diff still works', async () => {
    const t = setup(worktreeStatus({ files: [worktreeFile('src/a.ts'), staged('src/s.ts')] }))
    const w = await mountPanel(t, { readOnly: true })
    for (const sel of ['git-changes-stage-all', 'git-stage-selected']) {
      const el = q(sel) as HTMLButtonElement | null
      if (el) expect(el.disabled, sel).toBe(true)
    }
    expect((inRow('src/a.ts', 'git-row-stage') as HTMLButtonElement).disabled).toBe(true)
    expect(inRow('src/a.ts', 'git-row-stage')?.getAttribute('title')).toBe('읽기 전용')
    expect((inRow('src/a.ts', 'git-row-revert') as HTMLButtonElement).disabled).toBe(true)
    expect((inRow('src/s.ts', 'git-row-unstage', 'staged') as HTMLButtonElement).disabled).toBe(true)
    expect((q('git-commit') as HTMLButtonElement).disabled).toBe(true)
    expect((q('git-commit-ai') as HTMLButtonElement).disabled).toBe(true)
    expect((q('git-commit-message') as HTMLInputElement).disabled).toBe(true)
    await click(inRow('src/a.ts', 'git-row-open'))
    expect(t.wt.source.loadFileDiff).toHaveBeenCalledTimes(1)
    expect(q('git-center-diff-lines')).not.toBeNull()
    expect(w.emitted('open-diff')).toBeUndefined()
  })

  it('C25: a 403 on a write keeps every write disabled as 권한 없음', async () => {
    const t = setup(worktreeStatus({ files: [worktreeFile('src/a.ts'), worktreeFile('src/b.ts')] }))
    t.wt.source.stage.mockRejectedValueOnce(gitError(403, 'forbidden'))
    await mountPanel(t)
    await click(inRow('src/a.ts', 'git-row-stage'))
    expect(text('git-changes-notice')).toContain('권한 없음')
    expect((inRow('src/b.ts', 'git-row-stage') as HTMLButtonElement).disabled).toBe(true)
    expect(inRow('src/b.ts', 'git-row-stage')?.getAttribute('title')).toBe('권한 없음')
    expect((q('git-changes-stage-all') as HTMLButtonElement).disabled).toBe(true)
  })

  it('an E1 blocker disables only the operations it names, with its short reason', async () => {
    const t = setup(
      worktreeStatus({
        files: [worktreeFile('src/a.ts'), staged('src/s.ts')],
        blockers: [{ code: 'run_already_active', operations: ['stage', 'unstage', 'commit', 'revert', 'remove'] }],
      }),
    )
    await mountPanel(t)
    expect(inRow('src/a.ts', 'git-row-stage')?.getAttribute('title')).toBe('AI 실행 중 · Git 변경 잠금')
    expect((inRow('src/s.ts', 'git-row-unstage', 'staged') as HTMLButtonElement).disabled).toBe(true)
    expect((q('git-commit-message') as HTMLInputElement).disabled).toBe(true)
    expect(text('git-commit-reason')).toBe('AI 실행 중 · Git 변경 잠금')
    // E7 is not one of the operations a run blocks, but the commit area closes 🤖 with the commit's
    // reason while the commit is blocked (0067-R §4-5).
    expect((q('git-commit-ai') as HTMLButtonElement).disabled).toBe(true)
    expect(q('git-commit-ai')?.getAttribute('title')).toBe('AI 실행 중 · Git 변경 잠금')
  })
})

// ── Review 0020-TR rev0 (P1): a worktree confirm dialog is bound to its context ────────────
// A dialog opened in Flow A closes when the context moves on, and nothing it would have run is
// ever sent to B — even where B has the same path or the same index token.
describe('worktree confirm dialogs follow the context they were opened in', () => {
  const FLOW_B = { groupId: 'p.m.0002', name: '다른 Flow' }
  const mixed = () => worktreeStatus({ files: [worktreeFile('src/a.ts'), staged('s.ts'), added('src/n.ts')] })
  const dialogOpen = () => qa('.ui-confirm-message').length > 0

  it('revert: A dialog → Flow B → no request to B; B asks its own and sends to B', async () => {
    const t = setup(mixed())
    const w = await mountPanel(t)
    await click(inRow('src/a.ts', 'git-row-revert'))
    expect(dialogOpen()).toBe(true)
    await w.setProps({ flow: FLOW_B })
    await flushPromises()
    expect(dialogOpen()).toBe(false)
    expect(t.wt.source.revert).not.toHaveBeenCalled()
    // B lists the same path; its own dialog acts on B (the Git modeless closed with A).
    await openFiles()
    await click(inRow('src/a.ts', 'git-row-revert'))
    await confirmDialog()
    expect(t.wt.source.revert).toHaveBeenCalledTimes(1)
    expect(t.wt.source.revert.mock.calls[0][0]).toBe(FLOW_B.groupId)
  })

  it('remove: A dialog → another project → closed, nothing sent', async () => {
    const t = setup(mixed())
    const w = await mountPanel(t)
    await click(inRow('src/n.ts', 'git-row-remove'))
    expect(dialogOpen()).toBe(true)
    await w.setProps({ projectId: 'p2' })
    await flushPromises()
    expect(dialogOpen()).toBe(false)
    expect(t.wt.source.remove).not.toHaveBeenCalled()
    expect(t.wt.source.revert).not.toHaveBeenCalled()
  })

  it('commit: A snapshot → Flow B with the same index token → closed, no commit to B', async () => {
    const t = setup(mixed())
    const w = await mountPanel(t)
    await typeInto('git-commit-message', 'A commit')
    await click('git-commit')
    expect(confirmText()).toContain('Stage된 1개 파일')
    await w.setProps({ flow: FLOW_B })
    await flushPromises()
    expect(dialogOpen()).toBe(false)
    // B shows the same index token the A dialog fixed.
    expect(t.wt.source.loadStatus.mock.calls.at(-1)?.[0]).toBe(FLOW_B.groupId)
    expect(t.wt.db.status.indexToken).toBe('ix-1')
    expect(t.wt.source.commit).not.toHaveBeenCalled()
    await openFiles()
    expect((q('git-commit-message') as HTMLInputElement).value).toBe('')
  })

  it('AI overwrite: A dialog → the gate closes (failed git/status re-read) → closed, no E7', async () => {
    const t = setup(mixed())
    await mountPanel(t)
    await typeInto('git-commit-message', 'mine')
    await click('git-commit-ai')
    expect(confirmText()).toContain('입력한 메시지를 AI 제안으로 바꿀까요?')
    t.fake.source.loadStatus.mockRejectedValueOnce(gitError(500, 'git_error'))
    await click('git-refresh')
    expect(q('git-worktree')).toBeNull()
    expect(dialogOpen()).toBe(false)
    expect(t.wt.source.suggestCommitMessage).not.toHaveBeenCalled()
  })

  it('a dialog of the current context still runs; unmounting with one open sends nothing', async () => {
    const t = setup(mixed())
    const w = await mountPanel(t)
    await click(inRow('src/a.ts', 'git-row-revert'))
    await confirmDialog()
    expect(t.wt.source.revert).toHaveBeenCalledWith(G, ['src/a.ts'], 'wt-1')
    await click(inRow('src/n.ts', 'git-row-remove'))
    expect(dialogOpen()).toBe(true)
    w.unmount()
    wrapper = null
    await flushPromises()
    expect(dialogOpen()).toBe(false)
    expect(t.wt.source.remove).not.toHaveBeenCalled()
  })
})

describe('late write answers after a Flow switch (①)', () => {
  it('a late Stage answer of A never shows in B: not while B reads its first E1, not after', async () => {
    const t = setup(worktreeStatus({ files: [worktreeFile('src/a.ts'), worktreeFile('src/b.ts')] }))
    const gate = deferred<void>()
    const real = t.wt.source.stage.getMockImplementation()!
    t.wt.source.stage.mockImplementationOnce(async (...args) => {
      const result = await real(...args)
      await gate.promise
      return result
    })
    const w = await mountPanel(t)
    await click('git-changes-stage-all')
    expect(t.wt.source.stage).toHaveBeenCalledTimes(1)
    // B's first E1 is still on its way when A's answer arrives.
    const firstOfB = deferred<WorktreeStatus>()
    t.wt.source.loadStatus.mockImplementationOnce(() => firstOfB.promise)
    await w.setProps({ flow: { groupId: 'p.m.0002', name: '다른 Flow' } })
    await flushPromises()
    expect(q('git-worktree-loading')).not.toBeNull()
    gate.resolve()
    await flushPromises()
    expect(q('git-worktree-loading')).not.toBeNull()
    expect(q('git-changes')).toBeNull()
    expect(t.wt.source.loadFileDiff).not.toHaveBeenCalled()
    const ofB = worktreeStatus({ groupId: 'p.m.0002', worktreeToken: 'wt-b', files: [worktreeFile('b/x.ts'), worktreeFile('b/y.ts')] })
    // The fake keeps one worktree: from here on it is B's (opening the Git modeless reads it again).
    t.wt.db.status = ofB
    firstOfB.resolve(ofB)
    await flushPromises()
    await openFiles()
    expect(text('git-changes-count')).toBe('2')
    expect(text('git-changes-staged')).toBe('0')
    expect(q('git-changes-notice')).toBeNull()
    expect(w.emitted('badge-change')?.at(-1)).toEqual([{ tone: 'count', text: '2' }])
  })
})
