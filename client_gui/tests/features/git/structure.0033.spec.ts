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
// flowgate.v02.0067 T#4 — the Center Diff is the Git modeless `Diff` tab detail inside the part: it is
// off the public surface, reads through the GitSource (gitSource.ts only touches the project store at
// runtime) and only the right Dock assembles the part.
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
        'GitApplyAccordion.vue',
        'GitApplyInfo.vue',
        'GitMergeResultPush.vue',
        'GitCenterDiffView.vue',
        'GitChangeFolder.vue',
        'GitChangeRow.vue',
        'GitChangeSection.vue',
        'GitChangesAccordion.vue',
        'GitCommitBar.vue',
        'GitWorktreePanel.vue',
        // flowgate.v02.0067 T#1 — the Git modeless and its `상태` tab.
        'GitWorktreeStatus.vue',
        'GitWorktreeWindow.vue',
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
        'gateOperations.ts',
        'gitSource.ts',
        'gitWorktreeSession.ts',
        'index.ts',
        'types.ts',
        'worktreeList.ts',
        'worktreeReasons.ts',
      ].sort(),
    )
  })

  it('exposes only the panel at runtime (0067 T#4: the Center Diff is internal)', () => {
    expect(Object.keys(GitFeature)).toEqual(['DesignerGitPanel'])
  })

  it('reaches the server only through api.ts (actions) and features/project (reads)', () => {
    for (const file of FILES) {
      for (const spec of importsOf(read(file))) {
        if (/^@shared\/api$/.test(spec)) expect(rel(file), spec).toBe('api.ts')
        expect(spec, rel(file)).not.toMatch(/^pinia$|^vue-router$|^axios$/)
      }
    }
    // Runtime use of the project store (its loaders) is the HTTP source's only — the group file diff
    // too since 0067 T#4 (`worktree.loadFileDiff`, 0066 D#2 §5-1); the rest import types.
    const runtimeProjectImports = FILES.filter((file) =>
      /^\s*import\s+\{[^}]*\}\s+from\s+'@\/features\/project'/m.test(read(file)),
    ).map(rel)
    expect(runtimeProjectImports.sort()).toEqual(['gitSource.ts'])
  })

  it('imports only vue / vue-i18n, its own files, ui primitives, features/project and the shared Git helpers', () => {
    const allowed = [
      /^vue$/,
      /^vue-i18n$/,
      /^\.\/[A-Za-z]+(\.vue|\.css)?$/,
      /^\.\.\/\.\.\/components\/ui\/[A-Za-z]+\.vue$/,
      /^@\/features\/project$/,
      // D0007 §6: the gate operation adapter uses the document wire API and the shared event subscription.
      /^@\/features\/document$/,
      /^\.\.\/designer\/inspector\/useInspectorResource$/,
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
  // flowgate.v02.0066 T#6 — the Shell owned the Center Diff and handed it through the Center column
  // to the Workspace. flowgate.v02.0067 T#4 — the diff lives in the Git modeless, so the right Tool
  // Panel is again the only Designer file that knows the part (through the public surface).
  it('is assembled into the Designer by the right Tool Panel only, through the public surface', () => {
    const users = filesUnder(join(SRC, 'features/designer'))
      .filter((file) => /features\/git/.test(read(file)))
      .map((file) => relative(SRC, file).replace(/\\/g, '/'))
    expect(users).toEqual(['features/designer/shell/DesignerRightDock.vue'])
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
  // T#7 — also the merge / push / Fetch notices (APPLY_NOTICE_CODES and `code: a ? 'x' : 'y'`).
  it('has a text for every worktree notice and every disabled reason code', () => {
    const session = read(join(GIT, 'gitWorktreeSession.ts'))
    const codes = new Set<string>()
    for (const m of session.matchAll(/code: ([^,}\n]+)/g)) for (const q of m[1].matchAll(/'([a-z_]+)'/g)) codes.add(q[1])
    for (const name of ['WORKTREE_NOTICE_CODES', 'APPLY_NOTICE_CODES']) {
      const list = session.match(new RegExp(`const ${name}[^=]*=\\s*\\[([\\s\\S]*?)\\]`))?.[1] ?? ''
      expect(list, name).not.toBe('')
      for (const m of list.matchAll(/'([a-z_]+)'/g)) codes.add(m[1])
    }
    expect(codes.size).toBeGreaterThan(40)
    const ko = APP_MESSAGES.ko.git as unknown as { worktree: { notice: Record<string, string> }; reason: Record<string, string> }
    for (const code of codes) expect(ko.worktree.notice[code], code).toBeTruthy()
    const reasons = read(join(GIT, 'worktreeReasons.ts')).match(/const WORKTREE_REASON_CODES[^=]*=\s*\[([\s\S]*?)\]/)?.[1] ?? ''
    const reasonCodes = [...reasons.matchAll(/'([a-z_]+)'/g)].map((m) => m[1])
    expect(reasonCodes.length).toBeGreaterThan(30)
    for (const code of [...reasonCodes, 'unknown']) expect(ko.reason[code], code).toBeTruthy()
  })

  // flowgate.v02.0066 T#6 — none of the v19 mock-up's demo data, fixed branches or "(시안)" success texts.
  // T#7 (C15) — the Base apply parts too: no `v0.2` / `main` literal, no demo counters or gate.
  it('carries no mock-up demo data, fixed branch name or fake success text in the worktree area', () => {
    const files = FILES.filter((f) => /worktree|Worktree|GitChange|GitCommitBar|CenterDiff|GitApply|GitMergeResult/.test(rel(f)))
    expect(files.map(rel)).toEqual(expect.arrayContaining(['GitApplyAccordion.vue', 'GitApplyInfo.vue', 'GitMergeResultPush.vue']))
    for (const file of files) {
      expect(read(file), rel(file)).not.toMatch(
        /['"`]v0\.2['"`]|['"`]main['"`]|>\s*(v0\.2|main)\s*<|folderDefs|sampleNames|pendingCommits|mergedCommits|pushedCommits|\(시안\)|mockSwitch|gateText/,
      )
    }
  })

  // flowgate.v02.0066 T#7 — the Base apply area has no approval or commit button and never calls
  // the Base checkout push / fetch or finalize (0066 D#2 §6-4, §8 T#7 "하지 않는 것").
  it('the Base apply parts add no approval / commit action and no /git/push, /git/fetch or finalize call', () => {
    for (const name of ['GitApplyAccordion.vue', 'GitApplyInfo.vue', 'GitMergeResultPush.vue']) {
      const src = read(join(GIT, name))
      expect(src, name).not.toMatch(/approve|승인 요청|wt\.commit\b|commitSnapshot|finalize|pushBranch|GitApplyRequest|retryApply|applyNow/)
    }
    const session = read(join(GIT, 'gitWorktreeSession.ts'))
    expect(session).not.toMatch(/\bfinalize\(|pushBranch|src\(\)\.push\(|GitApplyRequest/)
  })
})
