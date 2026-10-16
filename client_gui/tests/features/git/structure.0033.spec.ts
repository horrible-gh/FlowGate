import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import * as GitFeature from '@/features/git'
import { APP_MESSAGES } from '@/app/i18n'

// flowgate.v02.0033 — source-level guards for the Designer Git part (0033-D §2, §7, §8.1).
// flowgate.v02.0066 T#6 — the Group worktree area and the Center Diff join the part (0066 D#2
// §2-2, §2-3): their files, the Center Diff's public entry points, the Center Diff's own read
// through the project store, `diffLines` from the Conversation surface, and the Designer Shell /
// Center column / Workspace that assemble the Center Diff through the public surface.
const ROOT = resolve(__dirname, '../../..')
const SRC = join(ROOT, 'src')
const GIT = join(SRC, 'features/git')

function filesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? filesUnder(full) : [full]
  })
}

const rel = (file: string) => relative(GIT, file).replace(/\\/g, '/')
const read = (file: string) => readFileSync(file, 'utf8')
const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[1])
const FILES = filesUnder(GIT)

function keysOf(obj: unknown, prefix = ''): string[] {
  if (!obj || typeof obj !== 'object') return [prefix]
  return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) => keysOf(v, prefix ? `${prefix}.${k}` : k))
}

describe('features/git structure (flowgate.v02.0033)', () => {
  it('holds the parts T0005 §1 lists', () => {
    expect(FILES.map(rel).sort()).toEqual(
      [
        'DesignerGitPanel.vue',
        'GitAiResolvingCard.vue',
        'GitCenterDiffView.vue',
        'GitChangeFolder.vue',
        'GitChangeRow.vue',
        'GitChangeSection.vue',
        'GitChangesAccordion.vue',
        'GitCommitBar.vue',
        'GitWorktreePanel.vue',
        'GitConflictCard.vue',
        'GitConflictWindow.vue',
        'GitConnectWindow.vue',
        'GitStatusErrorCard.vue',
        'GitUnlinkedCard.vue',
        'GitDirtyCard.vue',
        'GitExceptionList.vue',
        'GitFileRow.vue',
        'GitHeader.vue',
        'GitHoldCard.vue',
        'GitPushErrorWindow.vue',
        'GitPushFailureCard.vue',
        'GitReviewCard.vue',
        'api.ts',
        'gitCenterDiff.ts',
        'gitPanel.css',
        'gitSession.ts',
        'gitSource.ts',
        'gitWorktreeSession.ts',
        'index.ts',
        'types.ts',
        'worktreeList.ts',
        'worktreeReasons.ts',
      ].sort(),
    )
  })

  it('exposes only the panel and the Center Diff (session factory + screen) at runtime', () => {
    expect(Object.keys(GitFeature).sort()).toEqual(['DesignerGitPanel', 'GitCenterDiffView', 'createGitCenterDiff'].sort())
  })

  it('reaches the server only through api.ts (actions) and features/project (reads)', () => {
    for (const file of FILES) {
      for (const spec of importsOf(read(file))) {
        if (/^@shared\/api$/.test(spec)) expect(rel(file), spec).toBe('api.ts')
        expect(spec, rel(file)).not.toMatch(/^pinia$|^vue-router$|^axios$/)
      }
    }
    // Runtime use of the project store (its loaders) is the HTTP source's and the Center Diff's
    // default group diff loader (0066 D#2 §5-1); the rest import types.
    const runtimeProjectImports = FILES.filter((file) =>
      /^\s*import\s+\{[^}]*\}\s+from\s+'@\/features\/project'/m.test(read(file)),
    ).map(rel)
    expect(runtimeProjectImports.sort()).toEqual(['gitCenterDiff.ts', 'gitSource.ts'])
  })

  it('imports only vue / vue-i18n, its own files, ui primitives, features/project and the shared Git helpers', () => {
    const allowed = [
      /^vue$/,
      /^vue-i18n$/,
      /^\.\/[A-Za-z]+(\.vue|\.css)?$/,
      /^\.\.\/\.\.\/components\/ui\/[A-Za-z]+\.vue$/,
      /^@\/features\/project$/,
      // flowgate.v02.0066 T#6 — the Center Diff reuses the Conversation's pure line diff.
      /^@\/features\/conversation$/,
      /^@shared\/api$/,
      /^@shared\/gitErrors$/,
      /^@shared\/utils\/uuid$/,
    ]
    for (const file of FILES) {
      for (const spec of importsOf(read(file))) expect(allowed.some((re) => re.test(spec)), `${rel(file)}: ${spec}`).toBe(true)
    }
  })

  it('does not touch the v0.1 Git screens or their store (R §9.4)', () => {
    for (const file of FILES) {
      const src = read(file)
      expect(src, rel(file)).not.toMatch(/client\/src\/main|useExplorerStore|GitStatusPanel\.vue|GitMergeReviewDialog\.vue|fg:git_/)
    }
  })

  it('keeps the model, source and session free of screens', () => {
    for (const name of [
      'types.ts',
      'api.ts',
      'gitSource.ts',
      'gitSession.ts',
      'gitWorktreeSession.ts',
      'gitCenterDiff.ts',
      'worktreeList.ts',
      'worktreeReasons.ts',
    ]) {
      for (const spec of importsOf(read(join(GIT, name)))) expect(spec, name).not.toMatch(/\.vue$/)
    }
  })

  it('never sends the project default action or an omitted action to finalize', () => {
    const api = read(join(GIT, 'api.ts'))
    expect(api).not.toMatch(/default_action/)
    for (const file of FILES) expect(read(file), rel(file)).not.toMatch(/defaultAction|default_finalize_action/)
  })

  it('carries no push failure classification of its own', () => {
    for (const file of FILES) expect(read(file), rel(file)).not.toMatch(/non_ff|push_failure_kind|'(auth|permission|protected)'|diagnostic:/)
  })

  // flowgate.v02.0037 — the Designer right Tool Panel assembles the part through the public
  // surface, and only there.
  // flowgate.v02.0066 T#6 — the Shell owns the Center Diff and hands it through the Center column
  // to the Workspace, which draws it; all of them through the public surface only.
  it('is assembled into the Designer by the right Tool Panel and the Center Diff path, through the public surface', () => {
    const users = filesUnder(join(SRC, 'features/designer'))
      .filter((file) => /features\/git/.test(read(file)))
      .map((file) => relative(SRC, file).replace(/\\/g, '/'))
    expect(users.sort()).toEqual(
      [
        'features/designer/shell/DesignerCenterColumn.vue',
        'features/designer/shell/DesignerRightDock.vue',
        'features/designer/shell/DesignerShell.vue',
        'features/designer/shell/DesignerWorkspace.vue',
      ].sort(),
    )
    for (const user of users) {
      expect(importsOf(read(join(SRC, user))).filter((spec) => /features\/git/.test(spec)), user).toEqual(['@/features/git'])
    }
  })

  it('has the same git message keys in every locale', () => {
    const ko = keysOf(APP_MESSAGES.ko.git).sort()
    expect(ko.length).toBeGreaterThan(80)
    expect(keysOf(APP_MESSAGES.en.git).sort()).toEqual(ko)
    expect(keysOf(APP_MESSAGES.ja.git).sort()).toEqual(ko)
  })

  it('every literal gui.git key used in the part exists', () => {
    const ko = new Set(keysOf(APP_MESSAGES.ko.git).map((k) => `gui.git.${k}`))
    for (const file of FILES) {
      for (const m of read(file).matchAll(/['`](gui\.git\.[a-z_.]+)['`]/g)) {
        expect(ko.has(m[1]), `${rel(file)}: ${m[1]}`).toBe(true)
      }
    }
  })

  it('has a notice text for every code the session can raise', () => {
    const session = read(join(GIT, 'gitSession.ts'))
    const codes = new Set<string>()
    for (const m of session.matchAll(/code: '([a-z_]+)'/g)) codes.add(m[1])
    const list = session.match(/const NOTICE_CODES[^=]*=\s*\[([\s\S]*?)\]/)?.[1] ?? ''
    for (const m of list.matchAll(/'([a-z_]+)'/g)) codes.add(m[1])
    const notices = APP_MESSAGES.ko.git.notice as Record<string, string>
    for (const code of codes) expect(notices[code], code).toBeTruthy()
  })

  // flowgate.v02.0066 T#6 — every worktree notice and disabled reason code has its text.
  it('has a text for every worktree notice and every disabled reason code', () => {
    const session = read(join(GIT, 'gitWorktreeSession.ts'))
    const codes = new Set<string>()
    for (const m of session.matchAll(/code: '([a-z_]+)'/g)) codes.add(m[1])
    const list = session.match(/const WORKTREE_NOTICE_CODES[^=]*=\s*\[([\s\S]*?)\]/)?.[1] ?? ''
    for (const m of list.matchAll(/'([a-z_]+)'/g)) codes.add(m[1])
    expect(codes.size).toBeGreaterThan(20)
    const ko = APP_MESSAGES.ko.git as unknown as { worktree: { notice: Record<string, string> }; reason: Record<string, string> }
    for (const code of codes) expect(ko.worktree.notice[code], code).toBeTruthy()
    const reasons = read(join(GIT, 'worktreeReasons.ts')).match(/const WORKTREE_REASON_CODES[^=]*=\s*\[([\s\S]*?)\]/)?.[1] ?? ''
    const reasonCodes = [...reasons.matchAll(/'([a-z_]+)'/g)].map((m) => m[1])
    expect(reasonCodes.length).toBeGreaterThan(10)
    for (const code of [...reasonCodes, 'unknown']) expect(ko.reason[code], code).toBeTruthy()
  })

  // flowgate.v02.0066 T#6 — none of the v19 mock-up's demo data, fixed branches or "(시안)" success texts.
  it('carries no mock-up demo data, fixed branch name or fake success text in the worktree area', () => {
    for (const file of FILES.filter((f) => /worktree|Worktree|GitChange|GitCommitBar|CenterDiff/.test(rel(f)))) {
      expect(read(file), rel(file)).not.toMatch(/'v0\.2'|'main'|folderDefs|sampleNames|pendingCommits|\(시안\)|mockSwitch/)
    }
  })
})
