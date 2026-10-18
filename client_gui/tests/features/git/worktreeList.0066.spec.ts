import { describe, expect, it } from 'vitest'
import type { WorktreeFile } from '@/features/git/types'
import {
  buildSections,
  hiddenStagedCount,
  isWorktreeEmpty,
  pruneSelection,
  selectAllTargets,
  stagedProtectedRows,
  stageTargets,
  unstageTargets,
  WORKTREE_FOLDER_PAGE,
  type WorktreeListOptions,
} from '@/features/git/worktreeList'
import { worktreeFile, worktreeStatus } from './fakeWorktreeSource'

// flowgate.v02.0066 T#6 — the pure list of the changed-files accordion (0066 D#2 §3-6).
const options = (overrides: Partial<WorktreeListOptions> = {}): WorktreeListOptions => ({
  search: '',
  filter: 'all',
  folderMode: 'default',
  folderToggles: {},
  limits: {},
  ...overrides,
})

const staged = (path: string, overrides: Partial<WorktreeFile> = {}) =>
  worktreeFile(path, { staged: 'M', unstaged: null, canStage: false, canUnstage: true, ...overrides })
const added = (path: string, overrides: Partial<WorktreeFile> = {}) =>
  worktreeFile(path, { inHead: false, unstaged: null, untracked: true, canRevert: false, canRemove: true, ...overrides })

/** `folders` folders × `per` files, like the 1000-file case of C6. */
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

describe('worktreeList — sections and folders (§3-6(2))', () => {
  it('splits rows by their letters; a partly staged file is in both sections', () => {
    const status = worktreeStatus({
      files: [worktreeFile('src/a.ts'), staged('src/b.ts'), staged('src/c.ts', { unstaged: 'M', canStage: true }), added('n.ts')],
    })
    const [changes, stagedSection] = buildSections(status, options({ folderMode: 'open' }))
    expect(changes.key).toBe('changes')
    expect(changes.folders.flatMap((f) => f.rows.map((r) => `${r.letter} ${r.file.path}`))).toEqual(['? n.ts', 'M src/a.ts', 'M src/c.ts'])
    expect(stagedSection.key).toBe('staged')
    expect(stagedSection.folders.flatMap((f) => f.rows.map((r) => r.file.path))).toEqual(['src/b.ts', 'src/c.ts'])
    expect(changes.folders.map((f) => f.folder)).toEqual(['', 'src'])
  })

  it('1000 files: only the first folder opens, one page per folder, `더 보기` adds a page', () => {
    const status = worktreeStatus({ files: many(10, 100) })
    expect(status.counts.changed).toBe(1000)
    let [changes] = buildSections(status, options())
    expect(changes.total).toBe(1000)
    expect(changes.folders).toHaveLength(10)
    expect(changes.folders.map((f) => f.open)).toEqual([true, false, false, false, false, false, false, false, false, false])
    expect(changes.folders[0].rows).toHaveLength(WORKTREE_FOLDER_PAGE)
    expect(changes.folders[0].remaining).toBe(100 - WORKTREE_FOLDER_PAGE)
    expect(changes.folders.slice(1).every((f) => f.rows.length === 0 && f.count === 100)).toBe(true)
    ;[changes] = buildSections(status, options({ limits: { [changes.folders[0].key]: 2 * WORKTREE_FOLDER_PAGE } }))
    expect(changes.folders[0].rows).toHaveLength(20)
  })

  it('a search opens every matching folder and counts "matched / total"; folding then wins again', () => {
    const status = worktreeStatus({ files: many(4, 30) })
    const [changes] = buildSections(status, options({ search: 'FILE_027', folderToggles: { 'pkg/f00': false } }))
    expect(changes.matched).toBe(4)
    expect(changes.total).toBe(120)
    expect(changes.folders.every((f) => f.open && f.count === 1)).toBe(true)
    const [plain] = buildSections(status, options({ folderToggles: { 'pkg/f00': false, 'pkg/f02': true } }))
    expect(plain.folders.map((f) => f.open)).toEqual([false, false, true, false])
  })

  it('the folder menu opens or folds every folder', () => {
    const status = worktreeStatus({ files: many(3, 2) })
    expect(buildSections(status, options({ folderMode: 'open' }))[0].folders.every((f) => f.open)).toBe(true)
    expect(buildSections(status, options({ folderMode: 'closed' }))[0].folders.some((f) => f.open)).toBe(false)
  })

  it('filters by state: modified, added, new file, deleted, not operable', () => {
    const status = worktreeStatus({
      files: [
        worktreeFile('m.ts'),
        worktreeFile('d.ts', { unstaged: 'D' }),
        added('n.ts'),
        staged('a.ts', { staged: 'A', inHead: false }),
        worktreeFile('lock.ts', { fileClass: 'managed', protectedReason: 'managed', canStage: false, canRevert: false }),
      ],
    })
    const paths = (filter: WorktreeListOptions['filter']) =>
      buildSections(status, options({ filter, folderMode: 'open' })).flatMap((s) => s.folders.flatMap((f) => f.rows.map((r) => r.file.path)))
    expect(paths('M')).toEqual(['lock.ts', 'm.ts'])
    expect(paths('A')).toEqual(['a.ts'])
    expect(paths('?')).toEqual(['n.ts'])
    expect(paths('D')).toEqual(['d.ts'])
    expect(paths('locked')).toEqual(['lock.ts'])
  })
})

describe('worktreeList — hidden Stage (§3-6(0)) and selection (§3-6(4))', () => {
  it('a Stage no listed row carries is counted, never below 0, and the staged section exists for it alone', () => {
    const hiddenOnly = worktreeStatus({
      files: [],
      counts: { changed: 0, staged: 1, unstaged: 0, untracked: 0, excludedNewUntracked: 0, hidden: 1, managedDirty: 0, unsupported: 0 },
    })
    expect(hiddenStagedCount(hiddenOnly)).toBe(1)
    expect(isWorktreeEmpty(hiddenOnly)).toBe(false)
    const sections = buildSections(hiddenOnly, options())
    expect(sections.map((s) => [s.key, s.total, s.hiddenStaged])).toEqual([['staged', 0, 1]])
    const listedOnly = worktreeStatus({ files: [staged('a.ts')] })
    expect(hiddenStagedCount(listedOnly)).toBe(0)
    expect(hiddenStagedCount(worktreeStatus({ files: [staged('a.ts')], counts: { ...listedOnly.counts, staged: 0 } }))).toBe(0)
    expect(isWorktreeEmpty(worktreeStatus({ files: [] }))).toBe(true)
  })

  it('counts listed staged rows the server will not commit', () => {
    const status = worktreeStatus({
      files: [
        staged('ok.ts'),
        staged('mg.ts', { fileClass: 'managed', protectedReason: 'managed' }),
        staged('ex.ts', { fileClass: 'excluded', protectedReason: 'excluded_new' }),
        staged('u.ts', { staged: 'U', unsupported: 'unmerged' }),
      ],
    })
    expect(stagedProtectedRows(status)).toBe(3)
  })

  // flowgate.v02.0067 T#2 (0067-R §4-3): a managed / protected row is never checked, not even for its
  // Stage release — its own `−` and the full unstage stay its way out.
  it('selects only what a row may do: stage, or release its Stage — never a managed or protected row', () => {
    const status = worktreeStatus({
      files: [
        worktreeFile('a.ts'),
        worktreeFile('lock.ts', { fileClass: 'managed', protectedReason: 'managed', canStage: false, canRevert: false }),
        staged('mg.ts', { fileClass: 'managed', protectedReason: 'managed', canUnstage: true }),
        staged('both.ts', { unstaged: 'M', canStage: true }),
      ],
    })
    expect(selectAllTargets(status, '', 'all').sort()).toEqual(['a.ts', 'both.ts'])
    expect(selectAllTargets(status, 'a.ts', 'all')).toEqual(['a.ts'])
    const chosen = ['a.ts', 'mg.ts', 'both.ts', 'lock.ts']
    expect(stageTargets(chosen, status)).toEqual(['a.ts', 'both.ts'])
    expect(unstageTargets(chosen, status)).toEqual(['mg.ts', 'both.ts'])
    // After a status change only listed, still selectable paths stay.
    expect(pruneSelection(['a.ts', 'gone.ts', 'lock.ts', 'mg.ts'], status)).toEqual(['a.ts'])
  })
})
