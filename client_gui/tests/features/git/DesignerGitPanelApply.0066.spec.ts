import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import type { FlowChangeSet, ProjectGitStatus } from '@/features/project'
import { DesignerGitPanel } from '@/features/git'
import type { ManualMergeView } from '@/features/git/types'
import { G, P, createFakeGitSource, gitError, row, status } from './fakeGitSource'
import {
  createFakeWorktreeSource,
  manualMergeView,
  mergeResultRow,
  mergeResults,
  noDetails,
  worktreeStatus,
  type FakeWorktreeState,
} from './fakeWorktreeSource'

// flowgate.v02.0066 T#7 — the Base apply accordion in the real DesignerGitPanel with in-memory
// sources (0066 D#2 §3-4, §3-5, §6-4, §6-5): folded first look and header word, target choice and
// the "확인 중" state (C15), `머지` / `머지 + Push` and their dialogs (C16), approval never a reason
// (C17), merge results incl. push_rejected (C18), the follow-up Push part (C19), Fetch (C20), the
// no-workspace area (C22), the `Diff` pane intent (C14 b intent, C23 b·c), read-only / 403 (C25)
// and the merge button after a revert (C28).
// flowgate.v02.0067 T#3 — the tab body is no longer folded (no toggle, no header word): its count is
// `미반영 커밋`, only E8-admitted targets are listed and the follow-up Push shows only for a merge that
// really waits (DesignerGitPanelApply.0067 has the new rules).
// flowgate.v02.0067 T#4 — a `Diff` row opens its diff in the same tab (no `open-diff` /
// `git-center-diff-refresh`); the list pages by 16 with `이전` / `다음` (DesignerGitPanelDiff.0067).
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

function setup(wt: Partial<FakeWorktreeState> = {}, gitStatus?: ProjectGitStatus | null) {
  const fakeWt = createFakeWorktreeSource(wt)
  const fake = createFakeGitSource(gitStatus === undefined ? {} : { status: gitStatus }, { worktree: fakeWt.source })
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
const disabled = (sel: string) => (q(sel) as HTMLButtonElement | null)?.disabled

async function click(target: string | HTMLElement | null) {
  const el = typeof target === 'string' ? q(target) : target
  if (!el) throw new Error(`missing ${String(target)}`)
  el.click()
  await flushPromises()
}

async function choose(sel: string, value: string) {
  const el = q(sel) as HTMLSelectElement | null
  if (!el) throw new Error(`missing ${sel}`)
  el.value = value
  el.dispatchEvent(new Event('change'))
  await flushPromises()
}

async function check(sel: string, value: boolean) {
  const el = q(sel) as HTMLInputElement | null
  if (!el) throw new Error(`missing ${sel}`)
  el.checked = value
  el.dispatchEvent(new Event('change'))
  await flushPromises()
}

const dialogOpen = () => qa('.ui-confirm-message').length > 0
const confirmText = () => qa('.ui-confirm-message').map((e) => e.textContent).join(' ')
async function confirmDialog() {
  const button = qa('.ui-confirm-actions button').at(-1)
  if (!button) throw new Error('no confirm dialog')
  button.click()
  await flushPromises()
}

// flowgate.v02.0067 T#1 — the worktree parts live in the Git modeless the Git tab entries open;
// every tab stays mounted while it is open, the active one shown.
const entry = (tab: string) => document.body.querySelector<HTMLElement>(`[data-test="git-worktree-entry"][data-tab="${tab}"]`)
const windowTab = (tab: string) => document.body.querySelector<HTMLElement>(`[data-test="git-window-tab"][data-tab="${tab}"]`)
const panel = (tab: string) => document.body.querySelector<HTMLElement>(`[data-test="git-window-panel"][data-tab="${tab}"]`)

async function openTab(tab: string) {
  await click(windowTab(tab))
}

async function openApply() {
  await click(entry('merge'))
}

describe('Base apply — first look, header word, controls (R §4, C5 d)', () => {
  it('the `Base 반영` tab shows its body at once; its count is the E8 count of the chosen target', async () => {
    const t = setup({ merge: manualMergeView({ aheadOfTarget: 3 }) })
    await mountPanel(t)
    // Not in the Git tab itself: the `Base 반영` entry opens the modeless on its tab (0067-R §2-4).
    expect(q('git-apply')).toBeNull()
    expect(text('git-worktree-entry-count')).toBe('1개')
    await click(entry('merge'))
    const apply = q('git-apply')!
    expect(panel('merge')!.contains(apply)).toBe(true)
    expect(panel('files')!.contains(q('git-commit-bar'))).toBe(true)
    // 0067 T#3: no fold toggle and no header word any more.
    expect(q('git-apply-toggle')).toBeNull()
    expect(q('git-apply-status')).toBeNull()
    expect(text('git-apply-ahead')).toBe('3')
    expect(t.wt.source.loadTargetChanges).not.toHaveBeenCalled()
  })

  it('opened: target (E8-admitted targets, default marked), Fetch, 머지 후 Push, 머지 — no approval / commit button', async () => {
    const t = setup({
      merge: manualMergeView({
        targets: [
          { name: 'v0.2', kind: 'base', eligible: true, blockedReason: null, isWorkBase: true },
          { name: 'release', kind: 'local', eligible: true, blockedReason: null, isWorkBase: false },
          { name: 'ext', kind: 'local', eligible: false, blockedReason: 'remote_only', isWorkBase: false },
        ],
      }),
    })
    await mountPanel(t)
    await openApply()
    const options = qa('[data-test="git-apply-target"] option').map((o) => [(o as HTMLOptionElement).value, o.textContent?.trim(), (o as HTMLOptionElement).disabled])
    // 0067 T#3: a target E8 does not admit (here remote-only) is not listed (R §5-4).
    expect(options).toEqual([
      ['v0.2', 'v0.2 (기본)', false],
      ['release', 'release', false],
    ])
    expect((q('git-apply-target') as HTMLSelectElement).value).toBe('v0.2')
    expect(q('git-apply-fetch')?.getAttribute('title')).toBe('원격 정보 갱신(Fetch) — 작업 파일과 로컬 브랜치는 바뀌지 않습니다')
    expect((q('git-apply-push-after') as HTMLInputElement).checked).toBe(false)
    // `Diff` / `상태` are the modeless tabs now, not links of the Base apply body.
    expect(q('git-apply-diff')).toBeNull()
    expect(q('git-apply-state')).toBeNull()
    expect(text('git-apply-merge')).toBe('머지')
    expect(disabled('git-apply-merge')).toBe(false)
    const body = q('git-apply')!.textContent ?? ''
    expect(body).not.toMatch(/승인|커밋 ·|Commit/)
    expect(q('git-apply')!.querySelector('[data-test="git-commit"]')).toBeNull()
  })
})

describe('Base apply — target choice (C15)', () => {
  it('A→B: summary count is void and the header says "확인 중", merge off with a progress mark; a late A answer changes nothing', async () => {
    const t = setup()
    await mountPanel(t)
    await openApply()
    expect(text('git-worktree-summary-merge')).toBe('1')
    const held: Array<ReturnType<typeof deferred<ManualMergeView>>> = []
    t.wt.source.loadManualMerge.mockImplementation(() => {
      const answer = deferred<ManualMergeView>()
      held.push(answer)
      return answer.promise
    })
    await click('git-refresh')
    await choose('git-apply-target', 'release')
    expect(text('git-worktree-summary-merge')).toBe('—')
    expect(text('git-worktree-summary')).not.toContain('Base 미반영')
    expect(text('git-apply-ahead')).toBe('—')
    expect(disabled('git-apply-merge')).toBe(true)
    expect(q('git-apply-checking')).not.toBeNull()
    expect(q('git-apply-reason')).toBeNull()
    held[0].resolve(manualMergeView({ aheadOfTarget: 9 }))
    await flushPromises()
    expect(text('git-worktree-summary-merge')).toBe('—')
    held[1].resolve(manualMergeView({ aheadOfTarget: 4 }))
    await flushPromises()
    expect(text('git-worktree-summary-merge')).toBe('4')
    expect(text('git-worktree-summary')).not.toContain('release')
    expect(text('git-apply-ahead')).toBe('4')
    expect(disabled('git-apply-merge')).toBe(false)
    // A non-default target shows the E8 preview warnings.
    expect(qa('[data-test="git-apply-warning"]')).toHaveLength(0)
  })

  it('a non-default target shows its preview warnings; the default never does', async () => {
    const t = setup()
    await mountPanel(t)
    await openApply()
    t.wt.db.merge = manualMergeView({
      preview: {
        targetBranch: 'release',
        workBaseRef: 'v0.2',
        projectBaseBranch: 'v0.2',
        isWorkBase: false,
        isProjectBase: false,
        incomingWorkBaseCommits: null,
        warnings: ['target_differs_from_work_base', 'unmerge_unsupported_for_non_base_target'],
        error: null,
      },
    })
    await choose('git-apply-target', 'release')
    expect(qa('[data-test="git-apply-warning"]').map((e) => e.textContent?.trim())).toEqual([
      '⚠ 작업 기준과 다른 대상입니다.',
      '⚠ 이 대상의 머지는 되돌리기를 지원하지 않습니다.',
    ])
  })

  it('a merge dialog opened for A closes when the target changes and sends nothing (⑤ ⑥)', async () => {
    const t = setup()
    await mountPanel(t)
    await openApply()
    await click('git-apply-merge')
    expect(dialogOpen()).toBe(true)
    await choose('git-apply-target', 'release')
    expect(dialogOpen()).toBe(false)
    expect(t.wt.source.manualMerge).not.toHaveBeenCalled()
  })

  it('a dialog whose judgment was read again meanwhile sends nothing and says so', async () => {
    const t = setup()
    await mountPanel(t)
    await openApply()
    await click('git-apply-merge')
    await click('git-refresh')
    expect(dialogOpen()).toBe(true)
    await confirmDialog()
    expect(t.wt.source.manualMerge).not.toHaveBeenCalled()
    expect(text('git-apply-notice')).toContain('판정이 바뀌었습니다. 다시 확인하세요.')
  })
})

describe('Base apply — 머지 / 머지 + Push (C16, C17)', () => {
  it('unchecked: 머지 → dialog with the E8 count and target → E9 push=false', async () => {
    const t = setup({ merge: manualMergeView({ aheadOfTarget: 2 }) })
    await mountPanel(t)
    await openApply()
    await click('git-apply-merge')
    expect(confirmText()).toBe('2개 커밋을 v0.2에 머지합니다. 미커밋 파일은 포함되지 않습니다.')
    await confirmDialog()
    expect(t.wt.source.manualMerge).toHaveBeenCalledWith(G, 'v0.2', false)
    expect(text('git-apply-notice')).toContain('v0.2에 머지했습니다.')
  })

  it('checked: the label is 머지 + Push and the dialog / request push; a recovery says no new merge', async () => {
    const t = setup({ merge: manualMergeView({ aheadOfTarget: 0, recovery: true }) })
    await mountPanel(t)
    await openApply()
    await check('git-apply-push-after', true)
    expect(text('git-apply-merge')).toBe('머지 + Push')
    await click('git-apply-merge')
    expect(confirmText()).toBe('앞서 v0.2에 머지된 결과를 원격에 올리고 기록합니다. 새 머지는 만들지 않습니다.')
    await confirmDialog()
    expect(t.wt.source.manualMerge).toHaveBeenCalledWith(G, 'v0.2', true)
  })

  it('C17: pending review / final approval bound / open gate — merge on, no "승인 대기" anywhere', async () => {
    const t = setup(
      {
        merge: manualMergeView({
          approval: { acDocId: 'p.m.0001.0009-AC', status: 'pending_review', finalApprovalBound: true },
          graph: { runPresent: true, blocker: null, gateOpen: true },
        }),
      },
      status({ pending: [row({ status: 'waiting', mergeId: null, finalApprovalBound: true })] }),
    )
    await mountPanel(t)
    await openApply()
    expect(disabled('git-apply-merge')).toBe(false)
    expect(q('git-apply-reason')).toBeNull()
    await openTab('status')
    expect(text('git-status-state')).toBe('1개 커밋 미반영')
    expect(document.body.textContent).not.toContain('승인 대기')
  })

  it('an E8 block shows its one reason under the button (uncommitted with protected files)', async () => {
    const t = setup({
      merge: manualMergeView({
        canMerge: false,
        blocked: [{ code: 'uncommitted_changes', reason: null, details: noDetails({ count: 4, protectedUncommitted: 2 }) }],
        uncommitted: { blocking: 4, counts: { unstaged: 2, hidden: 2, excluded_new_untracked: 3 }, protectedUncommitted: 2, reason: null },
      }),
    })
    await mountPanel(t)
    await openApply()
    expect(disabled('git-apply-merge')).toBe(true)
    expect(text('git-apply-reason')).toBe(
      '미커밋 변경 4개 — 먼저 커밋하거나 되돌리세요 · Git 탭에서 처리할 수 없는 보호 파일 2개 — 작업자 또는 관리자에게 정리를 요청하세요',
    )
    // 0067 T#3: no "uncommitted … stay" guidance line (R §5-1).
    expect(q('git-apply-artifacts')).toBeNull()
    expect(q('git-apply')!.textContent).not.toContain('도구 잔재')
  })
})

describe('Base apply — merge results (C18)', () => {
  it('conflict: git/status is read and the 0033 conflict card appears above; the area says so and stays', async () => {
    const t = setup()
    await mountPanel(t)
    await openApply()
    t.fake.db.status = status({ pending: [row({ status: 'conflict', mergeId: 7 })] })
    t.wt.source.manualMerge.mockResolvedValueOnce({
      status: 'conflict',
      groupStatus: 'conflict',
      worktreeKept: true,
      action: 'merge_only',
      targetBranch: 'v0.2',
      mergeCommit: null,
      mergeCommitSha: null,
      pushed: false,
      recovered: false,
      mergeId: 7,
      conflictFileCount: 2,
      excludedArtifactCount: 0,
      jobId: 'job-9',
    })
    await click('git-apply-merge')
    await confirmDialog()
    expect(q('git-conflict-card')).not.toBeNull()
    expect(text('git-apply-notice')).toContain('충돌 — 위 카드에서 해결하세요.')
    expect(q('git-worktree')).not.toBeNull()
    expect(q('git-apply')).not.toBeNull()
  })

  it('push_rejected (new merge): no "되돌려졌", re-reads, then "이번 머지는 남아 있지 않음"; no 0033 push failure card', async () => {
    const t = setup({ merge: manualMergeView({ aheadOfTarget: 1 }) })
    await mountPanel(t)
    await openApply()
    await check('git-apply-push-after', true)
    t.wt.source.manualMerge.mockRejectedValueOnce(gitError(500, 'push_rejected'))
    const before = { git: t.fake.source.loadStatus.mock.calls.length, e1: t.wt.source.loadStatus.mock.calls.length }
    await click('git-apply-merge')
    await confirmDialog()
    expect(text('git-apply-notice')).toContain('원격이 Push를 거절했습니다. 현재 상태를 다시 읽었습니다.')
    expect(text('git-apply-reject-hint')).toBe('이번 머지는 v0.2에 남아 있지 않습니다. 원격 상태를 확인(Fetch)한 뒤 다시 시도하세요.')
    expect(document.body.textContent).not.toContain('되돌려졌')
    expect(t.fake.source.loadStatus.mock.calls.length).toBe(before.git + 1)
    expect(t.wt.source.loadStatus.mock.calls.length).toBe(before.e1 + 1)
    expect(q('git-push-card')).toBeNull()
    expect(t.wt.source.manualMerge).toHaveBeenCalledTimes(1)
  })

  it('push_rejected (unpushed merge recovery): the hint offers the same merge again; one press = one E9', async () => {
    const t = setup({ merge: manualMergeView({ aheadOfTarget: 0, recovery: true }) })
    await mountPanel(t)
    await openApply()
    await check('git-apply-push-after', true)
    t.wt.source.manualMerge.mockRejectedValueOnce(gitError(500, 'push_rejected'))
    t.wt.db.status = worktreeStatus({ gitState: { status: 'waiting', rootWfDone: false, finalApprovalBound: false } })
    t.wt.db.results = mergeResults({ groupStatus: 'waiting' })
    await click('git-apply-merge')
    await confirmDialog()
    expect(text('git-apply-reject-hint')).toBe(
      '로컬 v0.2에 원격에 올리지 못한 머지가 남아 있습니다. 머지 + Push로 같은 머지를 다시 올릴 수 있습니다(새 머지를 만들지 않음).',
    )
    expect(disabled('git-apply-merge')).toBe(false)
    await click('git-apply-merge')
    expect(confirmText()).toContain('새 머지는 만들지 않습니다')
    await confirmDialog()
    expect(t.wt.source.manualMerge).toHaveBeenCalledTimes(2)
    expect(t.wt.source.manualMerge).toHaveBeenLastCalledWith(G, 'v0.2', true)
    expect(q('git-push-card')).toBeNull()
  })

  it('already_recorded points at the follow-up Push row (highlighted)', async () => {
    const t = setup({ results: mergeResults({ results: [mergeResultRow({ mergeId: 11 })] }) })
    await mountPanel(t)
    await openApply()
    t.wt.source.manualMerge.mockRejectedValueOnce(
      Object.assign(gitError(409, 'nothing_to_merge'), {
        response: { status: 409, data: { ok: false, error: { code: 'nothing_to_merge', message: 'x', details: { reason: 'already_recorded', merge_id: 11 } } } },
      }),
    )
    await click('git-apply-merge')
    await confirmDialog()
    expect(text('git-apply-notice')).toContain('이미 머지된 결과입니다 — 아래 Push로 반영하세요.')
    expect(q('git-result-push')?.dataset.highlight).toBe('true')
  })
})

describe('Base apply — follow-up Push (C19)', () => {
  it('no result, or only published / moved rows: no Push part at all', async () => {
    const t = setup({
      results: mergeResults({
        results: [
          mergeResultRow({ mergeId: 1, pushAvailable: false, pushBlockedReason: 'already_published' }),
          mergeResultRow({ mergeId: 2, pushAvailable: false, pushBlockedReason: 'target_moved' }),
        ],
      }),
    })
    await mountPanel(t)
    await openApply()
    expect(q('git-result-push')).toBeNull()
    expect(text('git-apply-ahead')).toBe('1')
  })

  it('a row waiting behind the group Git job: a disabled Push and its reason (0067 T#3: unmeasured alone shows nothing)', async () => {
    const t = setup({ results: mergeResults({ results: [mergeResultRow({ pushAvailable: false, pushBlockedReason: 'group_blocked' })] }) })
    await mountPanel(t)
    await openApply()
    expect(q('git-result-push')?.dataset.mode).toBe('blocked')
    expect(disabled('git-result-push-button')).toBe(true)
    expect(text('git-result-push-reason')).toBe('진행 중인 Git 작업 뒤에 Push 가능')
  })

  it('one candidate: its target and one Push; the dialog names it and the other Base merges; E11 sends the record only', async () => {
    const t = setup(
      { results: mergeResults({ results: [mergeResultRow()] }) },
      status({
        unpushed: {
          count: 3,
          commitCount: 5,
          measured: true,
          remoteBranchMissing: false,
          localCommitCount: null,
          merges: [],
        },
      }),
    )
    await mountPanel(t)
    await openApply()
    expect(q('git-result-push')?.dataset.mode).toBe('one')
    expect(text('git-result-push-label')).toBe('원격 미반영 · v0.2')
    expect(q('git-result-push-choice')).toBeNull()
    await click('git-result-push-button')
    expect(confirmText()).toBe('v0.2에 머지 결과를 Push합니다. Worktree 직접 Push가 아닙니다. Base의 다른 미반영 머지 2개도 함께 올라갑니다.')
    await confirmDialog()
    expect(t.wt.source.pushMergeResult).toHaveBeenCalledWith(G, 11, 'a'.repeat(40))
    expect(t.fake.source.push).not.toHaveBeenCalled()
    expect(text('git-result-push-notice')).toContain('v0.2에 머지 결과를 Push했습니다.')
  })

  it('an unmeasured Base says so in the dialog', async () => {
    const t = setup(
      { results: mergeResults({ results: [mergeResultRow()] }) },
      status({ unpushed: { count: null, commitCount: null, measured: false, remoteBranchMissing: null, localCommitCount: 2, merges: [] } }),
    )
    await mountPanel(t)
    await openApply()
    await click('git-result-push-button')
    expect(confirmText()).toBe('v0.2에 머지 결과를 Push합니다. Worktree 직접 Push가 아닙니다. Base 원격 상태 확인 불가.')
  })

  it('two or more: a small choice (target · short commit · time), newest first; Push sends the chosen record', async () => {
    const t = setup({
      results: mergeResults({
        results: [
          mergeResultRow({
            mergeId: 12,
            target: { targetBranch: 'release', isProjectBase: false },
            mergeCommit: 'b'.repeat(40),
            completedAt: '2026-10-16T11:05:00+09:00',
          }),
          mergeResultRow({ mergeId: 11 }),
        ],
      }),
    })
    await mountPanel(t)
    await openApply()
    expect(q('git-result-push')?.dataset.mode).toBe('many')
    expect(qa('[data-test="git-result-push-choice"] option').map((o) => o.textContent?.trim())).toEqual([
      'release · bbbbbbb · 10-16 11:05',
      'v0.2 · aaaaaaa · 10-16 10:21',
    ])
    expect((q('git-result-push-choice') as HTMLSelectElement).value).toBe('12')
    await choose('git-result-push-choice', '11')
    await click('git-result-push-button')
    await confirmDialog()
    expect(t.wt.source.pushMergeResult).toHaveBeenCalledWith(G, 11, 'a'.repeat(40))
  })

  it('stale_target: E10 is read again, "다시 확인 후 Push", nothing resent', async () => {
    const t = setup({ results: mergeResults({ results: [mergeResultRow()] }) })
    await mountPanel(t)
    await openApply()
    const e10 = t.wt.source.loadMergeResults.mock.calls.length
    t.wt.source.pushMergeResult.mockRejectedValueOnce(gitError(409, 'stale_target'))
    await click('git-result-push-button')
    await confirmDialog()
    expect(text('git-result-push-notice')).toContain('기록이 바뀌어 다시 읽었습니다. 다시 확인 후 Push하세요.')
    expect(t.wt.source.loadMergeResults.mock.calls.length).toBe(e10 + 1)
    expect(t.wt.source.pushMergeResult).toHaveBeenCalledTimes(1)
    t.wt.source.pushMergeResult.mockRejectedValueOnce(gitError(500, 'push_rejected'))
    await click('git-result-push-button')
    await confirmDialog()
    expect(text('git-result-push-notice')).toContain('머지 결과는 그대로 남아 있습니다.')
  })
})

describe('Base apply — Fetch (C20), no workspace (C22), read-only / 403 (C25)', () => {
  it('Fetch: E12 only (never /git/fetch or /git/push), then git/status, E8 and E10 again', async () => {
    const t = setup()
    await mountPanel(t)
    await openApply()
    const e8 = t.wt.source.loadManualMerge.mock.calls.length
    const git = t.fake.source.loadStatus.mock.calls.length
    await click('git-apply-fetch')
    expect(t.wt.source.fetchRefs).toHaveBeenCalledWith(P, G)
    expect(t.fake.source.push).not.toHaveBeenCalled()
    expect(t.fake.source.loadStatus.mock.calls.length).toBe(git + 1)
    expect(t.wt.source.loadManualMerge.mock.calls.length).toBe(e8 + 1)
    expect(text('git-apply-notice')).toContain('원격 정보를 갱신했습니다(작업 파일은 바뀌지 않음).')
  })

  it('C22 no workspace: no file list or commit line; Base apply has Fetch and the follow-up Push only', async () => {
    const t = setup({
      status: worktreeStatus({ worktreeAvailable: false, files: [], worktreeToken: null, indexToken: null }),
      results: mergeResults({ results: [mergeResultRow()] }),
    })
    await mountPanel(t)
    expect(q('git-changes')).toBeNull()
    expect(q('git-commit-bar')).toBeNull()
    await click(entry('merge'))
    expect(q('git-changes')).toBeNull()
    expect(q('git-commit-bar')).toBeNull()
    // 0067 T#3: one short line instead of the explanation, and no count without a workspace.
    expect(text('git-apply-unavailable')).toBe('작업 공간 없음')
    expect(q('git-apply-stats')).toBeNull()
    expect(q('git-apply-target')).toBeNull()
    expect(q('git-apply-merge')).toBeNull()
    expect(q('git-apply-push-after')).toBeNull()
    expect(disabled('git-apply-fetch')).toBe(false)
    expect(q('git-result-push')?.dataset.mode).toBe('one')
    await click('git-apply-fetch')
    expect(t.wt.source.fetchRefs).toHaveBeenCalledTimes(1)
  })

  it('C25 read-only: merge, Push and Fetch off with 읽기 전용; the panes still read', async () => {
    const t = setup({ results: mergeResults({ results: [mergeResultRow()] }) })
    await mountPanel(t, { readOnly: true })
    await openApply()
    expect([disabled('git-apply-merge'), disabled('git-result-push-button'), disabled('git-apply-fetch')]).toEqual([true, true, true])
    expect(text('git-apply-reason')).toBe('읽기 전용')
    expect(q('git-apply-fetch')?.getAttribute('title')).toBe('읽기 전용')
    await openTab('diff')
    expect(t.wt.source.loadTargetChanges).toHaveBeenCalledTimes(1)
  })

  it('C25 a 403 on a merge keeps every write off as 권한 없음', async () => {
    const t = setup()
    await mountPanel(t)
    await openApply()
    t.wt.source.manualMerge.mockRejectedValueOnce(gitError(403, 'forbidden'))
    await click('git-apply-merge')
    await confirmDialog()
    expect(text('git-apply-reason')).toBe('권한 없음')
    expect(disabled('git-apply-fetch')).toBe(true)
    await openTab('files')
    expect(disabled('git-commit')).toBe(true)
  })
})

describe('Base apply — the `Diff` / `상태` tabs of the Git modeless (C14 b intent, C23 b·c)', () => {
  const list = (target: string, paths: Array<[string, string]>): FlowChangeSet => ({
    groupId: G,
    branch: 'flowgate_v02_0008',
    commit: 'h1',
    baseBranch: target,
    changes: paths.map(([path, letter]) => ({ path, status: letter })),
  })

  it('`Diff`: the chosen target list; a row opens its diff against that target in the tab; a target change closes it', async () => {
    const t = setup({ targetChanges: { 'v0.2': list('v0.2', [['src/a.ts', 'M']]), release: list('release', [['r.ts', 'A'], ['x.ts', 'R']]) } })
    const w = await mountPanel(t)
    await openApply()
    await openTab('diff')
    expect(text('git-apply-diff-title')).toBe('v0.2 기준 변경 1개')
    await openTab('merge')
    await choose('git-apply-target', 'release')
    await openTab('diff')
    expect(t.wt.source.loadTargetChanges.mock.calls.map((c) => c[2])).toEqual(['v0.2', 'release'])
    expect(qa('[data-test="git-apply-diff-row"]').map((r) => r.dataset.path)).toEqual(['r.ts', 'x.ts'])
    await click(qa('[data-test="git-apply-diff-row"]')[0])
    expect(text('git-center-diff-path')).toBe('r.ts')
    expect(text('git-center-diff-compare')).toBe('release 기준 ↔ 작업 파일')
    await click('git-center-diff-back')
    expect(q('git-window')?.dataset.tab).toBe('diff')
    await click(qa('[data-test="git-apply-diff-row"]')[1])
    expect(t.wt.source.loadFileDiff.mock.calls).toEqual([
      [P, G, 'r.ts', 'release'],
      [P, G, 'x.ts', 'release'],
    ])
    expect(w.emitted('open-diff')).toBeUndefined()
    // (c) a target change closes the comparison diff: the new target's list is back.
    await openTab('merge')
    await choose('git-apply-target', 'v0.2')
    await openTab('diff')
    expect(q('git-center-diff')).toBeNull()
    expect(qa('[data-test="git-apply-diff-row"]').map((r) => r.dataset.path)).toEqual(['src/a.ts'])
    expect(w.emitted('git-center-diff-refresh')).toBeUndefined()
  })

  it('`Diff` pages by 16 with `이전` / `다음`; another tab and back keeps the page without reading again', async () => {
    const paths = Array.from({ length: 23 }, (_, i) => [`f${String(i).padStart(2, '0')}.ts`, 'M'] as [string, string])
    const t = setup({ targetChanges: { 'v0.2': list('v0.2', paths) } })
    await mountPanel(t)
    await click(entry('diff'))
    expect(qa('[data-test="git-apply-diff-row"]')).toHaveLength(16)
    expect(text('git-apply-diff-range')).toBe('1–16 / 23')
    expect([disabled('git-apply-diff-prev'), disabled('git-apply-diff-next')]).toEqual([true, false])
    expect(q('git-apply-diff-more')).toBeNull()
    await click('git-apply-diff-next')
    expect(qa('[data-test="git-apply-diff-row"]').map((r) => r.dataset.path)).toEqual(paths.slice(16).map(([p]) => p))
    expect(text('git-apply-diff-range')).toBe('17–23 / 23')
    expect([disabled('git-apply-diff-prev'), disabled('git-apply-diff-next')]).toEqual([false, true])
    await openTab('status')
    expect(panel('status')!.style.display).toBe('')
    expect(panel('diff')!.style.display).toBe('none')
    await openTab('diff')
    expect(qa('[data-test="git-apply-diff-row"]')).toHaveLength(7)
    expect(t.wt.source.loadTargetChanges).toHaveBeenCalledTimes(1)
  })

  it('`상태`: the v27 numbers and rows from the values already read; a voided judgment never shows the old ones', async () => {
    const t = setup({ merge: manualMergeView({ aheadOfTarget: 2 }) })
    await mountPanel(t)
    await click(entry('status'))
    expect([text('git-status-changed'), text('git-status-staged'), text('git-status-ahead')]).toEqual(['1', '0', '2'])
    expect(text('git-status-worktree')).toBe('flowgate_v02_0008')
    expect(text('git-status-base')).toBe('v0.2')
    expect(text('git-status-target')).toBe('v0.2 (기본)')
    expect(text('git-status-state')).toBe('2개 커밋 미반영')
    // Nothing beyond the v27 items (0067-R §7).
    expect(qa('[data-test="git-status"] .gw-status-metric')).toHaveLength(3)
    expect(qa('[data-test="git-status"] .gw-status-row')).toHaveLength(4)
    const held = deferred<ManualMergeView>()
    t.wt.source.loadManualMerge.mockImplementationOnce(() => held.promise)
    await openTab('merge')
    await choose('git-apply-target', 'release')
    await openTab('status')
    expect(text('git-status-ahead')).toBe('—')
    expect(text('git-status-target')).toBe('release')
    expect(text('git-status-state')).toBe('확인 중')
    held.resolve(manualMergeView({ aheadOfTarget: 5 }))
    await flushPromises()
    expect(text('git-status-ahead')).toBe('5')
  })

  it('`상태`: a failed E8 read says so', async () => {
    const t = setup()
    t.wt.source.loadManualMerge.mockRejectedValue(gitError(500, 'git_error'))
    await mountPanel(t)
    await click(entry('status'))
    expect(text('git-status-ahead')).toBe('—')
    expect(text('git-status-failed')).toBe('머지 판정을 읽지 못했습니다.')
  })
})

describe('Base apply — the merge button after a revert (C28)', () => {
  it('reverting the last uncommitted file turns 머지 on without a refresh', async () => {
    const t = setup({
      merge: manualMergeView({
        canMerge: false,
        aheadOfTarget: 2,
        blocked: [{ code: 'uncommitted_changes', reason: null, details: noDetails({ count: 1 }) }],
        uncommitted: { blocking: 1, counts: { unstaged: 1 }, protectedUncommitted: 0, reason: null },
      }),
    })
    await mountPanel(t)
    await openApply()
    expect(disabled('git-apply-merge')).toBe(true)
    expect(text('git-apply-reason')).toBe('미커밋 변경 1개 — 먼저 커밋하거나 되돌리세요')
    t.wt.db.merge = manualMergeView({ aheadOfTarget: 2 })
    await openTab('files')
    const revert = document.body.querySelector<HTMLElement>('[data-test="git-row"][data-path="src/a.ts"] [data-test="git-row-revert"]')
    await click(revert)
    await confirmDialog()
    await openTab('merge')
    expect(disabled('git-apply-merge')).toBe(false)
    expect(q('git-apply-reason')).toBeNull()
    expect(text('git-apply-ahead')).toBe('2')
  })
})

describe('Base apply — a Flow switch (C23 a for the apply part)', () => {
  it('turns 머지 후 Push OFF and closes a merge dialog and the Git modeless', async () => {
    const t = setup()
    const w = await mountPanel(t)
    await openApply()
    await check('git-apply-push-after', true)
    await click('git-apply-merge')
    expect(dialogOpen()).toBe(true)
    await w.setProps({ flow: { groupId: 'p.m.0002', name: 'B' } })
    await flushPromises()
    expect(dialogOpen()).toBe(false)
    // 0067 T#1: another Flow is another worktree — the Git modeless closes too.
    expect(q('git-window')).toBeNull()
    await click(entry('merge'))
    expect((q('git-apply-push-after') as HTMLInputElement).checked).toBe(false)
    expect(text('git-apply-merge')).toBe('머지')
    expect(t.wt.source.manualMerge).not.toHaveBeenCalled()
  })
})

// Static guard for C15: the merge target is never a typed or fixed branch.
describe('Base apply — no branch input', () => {
  it('has no text input in the Base apply body', async () => {
    const t = setup()
    await mountPanel(t)
    await openApply()
    expect(q('git-apply')!.querySelectorAll('input[type="text"], input:not([type])')).toHaveLength(0)
  })
})

