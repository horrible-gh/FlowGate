import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import type { ManualMergeTarget } from '@/features/git/types'
import { followUpPushMode, mergeTargetChoices } from '@/features/git/worktreeReasons'
import { G, P, createFakeGitSource } from './fakeGitSource'
import {
  createFakeWorktreeSource,
  manualMergeView,
  mergeResultRow,
  mergeResults,
  noDetails,
  type FakeWorktreeState,
} from './fakeWorktreeSource'

// flowgate.v02.0067 T#3 — the `Base 반영` tab of the Git modeless (0067-R §5, v27 `modalMergeHTML`)
// in the real DesignerGitPanel with in-memory sources: the `미반영 커밋` count alone and no guidance
// line (§5-1), the target row and the `머지 후 Push` switch on one label / input start column (§5-2),
// the ON/OFF switch, OFF at first, and the `머지` / `머지 + Push` label and request (§5-3), only
// E8-admitted targets listed — internal work branches hidden by the server's `internal_slot`, not
// by a name (§5-4), Fetch as a small icon and the unchanged merge judgment (§5-5), and the
// follow-up Push only while a merge really waits for its remote (§5-6).
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

function setup(wt: Partial<FakeWorktreeState> = {}, props: Record<string, unknown> = {}) {
  const fakeWt = createFakeWorktreeSource(wt)
  const fake = createFakeGitSource({}, { worktree: fakeWt.source })
  return { wt: fakeWt, fake, props }
}

async function openMerge(t: ReturnType<typeof setup>) {
  wrapper = mount(DesignerGitPanel, {
    props: { projectId: P, flow: FLOW, source: t.fake.source, ...t.props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  await click(document.body.querySelector<HTMLElement>('[data-test="git-worktree-entry"][data-tab="merge"]'))
}

const q = (sel: string) => document.body.querySelector<HTMLElement>(`[data-test="${sel}"]`)
const qa = (sel: string) => [...document.body.querySelectorAll<HTMLElement>(sel)]
const text = (sel: string) => q(sel)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
const disabled = (sel: string) => (q(sel) as HTMLButtonElement | null)?.disabled
const options = () => qa('[data-test="git-apply-target"] option').map((o) => (o as HTMLOptionElement).value)
const pushSwitch = () => q('git-apply-push-after') as HTMLInputElement

async function click(target: string | HTMLElement | null) {
  const el = typeof target === 'string' ? q(target) : target
  if (!el) throw new Error(`missing ${String(target)}`)
  el.click()
  await flushPromises()
}

async function choose(sel: string, value: string) {
  const el = q(sel) as HTMLSelectElement
  el.value = value
  el.dispatchEvent(new Event('change'))
  await flushPromises()
}

// A click on the switch (its label toggles the native checkbox, which fires `change`).
async function toggleSwitch() {
  await click(q('git-apply-push-row'))
}

const confirmText = () => qa('.ui-confirm-message').map((e) => e.textContent).join(' ')
async function confirmDialog() {
  const button = qa('.ui-confirm-actions button').at(-1)
  if (!button) throw new Error('no confirm dialog')
  button.click()
  await flushPromises()
}

const css = () => readFileSync(join(resolve(__dirname, '../../..'), 'src/features/git/gitPanel.css'), 'utf8')
const rule = (selector: string) => {
  const escaped = selector.replace(/[.]/g, '\\.').replace(/,\s*/g, ',\\s*')
  // The block whose selector list is exactly `selector` (after a `}` or a comment, never mid-list).
  return css().match(new RegExp(`(?:^|[}/])\\s*${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? ''
}

const target = (name: string, eligible: boolean, blockedReason: string | null, isWorkBase = false): ManualMergeTarget => ({
  name,
  kind: name === 'v0.2' ? 'base' : 'local',
  eligible,
  blockedReason,
  isWorkBase,
})

// E8 candidates of a project with FlowGate slots: two internal work branches (one without any
// FlowGate-like name), a user branch that only looks like one, and other refused targets.
const MIXED: ManualMergeTarget[] = [
  target('feature-x', false, 'internal_slot'),
  target('flowgate_v02_0067', false, 'internal_slot'),
  target('flowgate_v02_9999', true, null),
  target('only-remote', false, 'remote_only'),
  target('release', true, null),
  target('shared', false, 'workspace_busy'),
  target('theirs', false, 'owner_mismatch'),
  target('v0.2', true, null, true),
]

describe('Base 반영 — the count and no guidance line (R §5-1)', () => {
  it('shows the 미반영 커밋 count of the chosen target only; no uncommitted / tool artifact guidance', async () => {
    const t = setup({
      merge: manualMergeView({
        aheadOfTarget: 3,
        uncommitted: { blocking: 0, counts: { excluded_new_untracked: 4 }, protectedUncommitted: 0, reason: null },
      }),
    })
    await openMerge(t)
    expect(text('git-apply-stats')).toBe('미반영 커밋3')
    expect(qa('[data-test="git-apply-stats"] strong')).toHaveLength(1)
    expect(text('git-apply-ahead')).toBe('3')
    const body = q('git-apply')!.textContent ?? ''
    for (const phrase of ['커밋하지 않은', '미커밋 파일', '도구 잔재', '작업 공간에 남습니다', 'Base 반영']) expect(body).not.toContain(phrase)
    expect(q('git-apply-reason')).toBeNull()
  })

  it('nothing to merge: the count says 0, 머지 is off and its reason stays in the tooltip, not as a line', async () => {
    const t = setup({
      merge: manualMergeView({
        canMerge: false,
        aheadOfTarget: 0,
        mergeNeeded: false,
        blocked: [{ code: 'nothing_to_merge', reason: null, details: noDetails() }],
      }),
    })
    await openMerge(t)
    expect(text('git-apply-ahead')).toBe('0')
    expect(disabled('git-apply-merge')).toBe(true)
    expect(q('git-apply-merge')!.getAttribute('title')).toBe('반영할 커밋 없음')
    expect(q('git-apply-reason')).toBeNull()
    expect(q('git-apply')!.textContent).not.toContain('반영할 커밋 없음')
  })

  it('any other refusal keeps its one line (the existing judgment)', async () => {
    const t = setup({
      merge: manualMergeView({ canMerge: false, blocked: [{ code: 'git_busy', reason: null, details: noDetails() }] }),
    })
    await openMerge(t)
    expect(disabled('git-apply-merge')).toBe(true)
    expect(text('git-apply-reason')).toBe('다른 Git 작업 진행 중')
  })
})

describe('Base 반영 — one label column and one input start column (R §5-2)', () => {
  it('the target row is [label, select, Fetch icon] and the switch row [label, switch], each label naming its control', async () => {
    const t = setup()
    await openMerge(t)
    const targetRow = q('git-apply-target-row')!
    const switchRow = q('git-apply-push-row')!
    expect([...targetRow.children].map((e) => e.tagName)).toEqual(['LABEL', 'SELECT', 'BUTTON'])
    expect([...switchRow.children].map((e) => e.className)).toEqual(['', 'gw-merge-switch'])
    expect(targetRow.className).toBe('gw-merge-field')
    expect(switchRow.className).toBe('gw-merge-switch-row')
    // Both rows are direct children of the tab body: the same left edge.
    expect(targetRow.parentElement).toBe(q('git-apply'))
    expect(switchRow.parentElement).toBe(q('git-apply'))
    const label = targetRow.querySelector('label')!
    expect(label.textContent).toBe('머지 대상')
    expect(label.getAttribute('for')).toBe(q('git-apply-target')!.id)
    expect(switchRow.getAttribute('for')).toBe(pushSwitch().id)
    expect([...(pushSwitch().labels ?? [])].map((l) => l.textContent?.trim())).toEqual(['머지 후 Push'])
  })

  it('CSS: both rows use the same label width, gap and row height; the switch sits at the input start', () => {
    expect(rule('.gw-apply')).toMatch(/--gw-merge-label:\s*90px/)
    const shared = rule('.gw-merge-field, .gw-merge-switch-row')
    expect(shared).toMatch(/display:\s*grid/)
    expect(shared).toMatch(/column-gap:\s*var\(--gw-merge-gap\)/)
    expect(shared).toMatch(/min-height:\s*36px/)
    expect(shared).toMatch(/font-size:\s*11\.5px/)
    expect(rule('.gw-merge-field')).toMatch(/grid-template-columns:\s*var\(--gw-merge-label\) minmax\(0, 1fr\) 35px/)
    expect(rule('.gw-merge-switch-row')).toMatch(/grid-template-columns:\s*var\(--gw-merge-label\) 43px/)
    expect(rule('.gw-merge-switch-row')).toMatch(/justify-content:\s*start/)
    expect(rule('.gw-merge-submit')).toMatch(/width:\s*100%/)
  })

  it('Fetch is the small icon button at the end of the target row; it sends E12 only', async () => {
    const t = setup()
    await openMerge(t)
    const fetch = q('git-apply-fetch')!
    expect(fetch.parentElement).toBe(q('git-apply-target-row'))
    expect(fetch.classList.contains('gw-iconbtn')).toBe(true)
    expect(fetch.textContent).toBe('⟳')
    expect(fetch.getAttribute('aria-label')).toBe('Fetch')
    await click(fetch)
    expect(t.wt.source.fetchRefs).toHaveBeenCalledWith(P, G)
    expect(t.fake.source.push).not.toHaveBeenCalled()
  })
})

describe('Base 반영 — the 머지 후 Push switch (R §5-3)', () => {
  it('is an ON/OFF switch, OFF at first; the button reads 머지 and sends push=false', async () => {
    const t = setup({ merge: manualMergeView({ aheadOfTarget: 2 }) })
    await openMerge(t)
    const input = pushSwitch()
    expect(input.type).toBe('checkbox')
    expect(input.getAttribute('role')).toBe('switch')
    expect(input.checked).toBe(false)
    expect(q('git-apply-push-row')!.querySelector('.gw-merge-switch-track')).not.toBeNull()
    expect(text('git-apply-merge')).toBe('머지')
    await click('git-apply-merge')
    expect(confirmText()).toBe('2개 커밋을 v0.2에 머지합니다. 미커밋 파일은 포함되지 않습니다.')
    await confirmDialog()
    expect(t.wt.source.manualMerge).toHaveBeenCalledWith(G, 'v0.2', false)
  })

  it('ON: the button reads 머지 + Push and the dialog / request push; OFF again: back to 머지', async () => {
    const t = setup({ merge: manualMergeView({ aheadOfTarget: 2 }) })
    await openMerge(t)
    await toggleSwitch()
    expect(pushSwitch().checked).toBe(true)
    expect(text('git-apply-merge')).toBe('머지 + Push')
    await click('git-apply-merge')
    expect(confirmText()).toBe('2개 커밋을 v0.2에 머지하고 원격에 Push합니다. 미커밋 파일은 포함되지 않습니다.')
    await confirmDialog()
    expect(t.wt.source.manualMerge).toHaveBeenLastCalledWith(G, 'v0.2', true)
    await toggleSwitch()
    expect(pushSwitch().checked).toBe(false)
    expect(text('git-apply-merge')).toBe('머지')
  })

  it('the choice survives a switch to another tab and back', async () => {
    const t = setup()
    await openMerge(t)
    await toggleSwitch()
    await click(document.body.querySelector<HTMLElement>('[data-test="git-window-tab"][data-tab="files"]'))
    await click(document.body.querySelector<HTMLElement>('[data-test="git-window-tab"][data-tab="merge"]'))
    expect(pushSwitch().checked).toBe(true)
    expect(text('git-apply-merge')).toBe('머지 + Push')
  })
})

describe('Base 반영 — merge targets: allowed branches only (R §5-4)', () => {
  it('lists only E8-admitted targets: internal work branches (by internal_slot, whatever their name) and other refused ones are hidden', async () => {
    const t = setup({ merge: manualMergeView({ targets: MIXED }) })
    await openMerge(t)
    expect(options()).toEqual(['flowgate_v02_9999', 'release', 'v0.2'])
    expect(qa('[data-test="git-apply-target"] option').map((o) => o.textContent?.trim())).toEqual([
      'flowgate_v02_9999',
      'release',
      'v0.2 (기본)',
    ])
    expect((q('git-apply-target') as HTMLSelectElement).value).toBe('v0.2')
    expect(q('git-apply')!.textContent).not.toMatch(/feature-x|flowgate_v02_0067|내부 작업 브랜치/)
  })

  it('a chosen target the server now marks internal leaves the choice: "대상 없음", 머지 off, the reason in the tooltip only', async () => {
    const t = setup()
    await openMerge(t)
    await choose('git-apply-target', 'release')
    expect((q('git-apply-target') as HTMLSelectElement).value).toBe('release')
    t.wt.db.merge = manualMergeView({ targets: [target('v0.2', true, null, true), target('release', false, 'internal_slot')] })
    await click('git-refresh')
    expect(options()).toEqual(['', 'v0.2'])
    expect((q('git-apply-target') as HTMLSelectElement).value).toBe('')
    expect(text('git-apply-target')).toContain('대상 없음')
    expect(disabled('git-apply-merge')).toBe(true)
    expect(q('git-apply-merge')!.getAttribute('title')).toBe('머지 대상 없음')
    expect(q('git-apply-reason')).toBeNull()
    expect(t.wt.source.manualMerge).not.toHaveBeenCalled()
  })

  it('mergeTargetChoices keeps the admitted targets in order and never drops one by its name', () => {
    expect(mergeTargetChoices(MIXED).map((x) => x.name)).toEqual(['flowgate_v02_9999', 'release', 'v0.2'])
    // Defensive: an internal slot is never offered even if a payload called it eligible.
    expect(mergeTargetChoices([target('slot', true, 'internal_slot'), target('main', true, null)]).map((x) => x.name)).toEqual(['main'])
    expect(mergeTargetChoices([])).toEqual([])
  })
})

describe('Base 반영 — the follow-up Push only while a merge waits for its remote (R §5-6)', () => {
  it('no merge result, or results already on the remote / moved / unmeasured: no follow-up Push part', async () => {
    const t = setup({
      results: mergeResults({
        results: [
          mergeResultRow({ mergeId: 1, pushAvailable: false, pushBlockedReason: 'already_published' }),
          mergeResultRow({ mergeId: 2, pushAvailable: false, pushBlockedReason: 'target_moved' }),
          mergeResultRow({ mergeId: 3, pushAvailable: false, pushBlockedReason: 'unmeasured' }),
        ],
      }),
    })
    await openMerge(t)
    expect(q('git-result-push')).toBeNull()
    expect(q('git-result-push-reason')).toBeNull()
  })

  it('a merge with the switch OFF that leaves a remote wait shows the follow-up Push; Push sends that record', async () => {
    const t = setup({ merge: manualMergeView({ aheadOfTarget: 1 }) })
    await openMerge(t)
    expect(q('git-result-push')).toBeNull()
    t.wt.db.results = mergeResults({ results: [mergeResultRow()] })
    await click('git-apply-merge')
    await confirmDialog()
    expect(t.wt.source.manualMerge).toHaveBeenCalledWith(G, 'v0.2', false)
    expect(q('git-result-push')?.dataset.mode).toBe('one')
    expect(text('git-result-push-label')).toBe('원격 미반영 · v0.2')
    await click('git-result-push-button')
    await confirmDialog()
    expect(t.wt.source.pushMergeResult).toHaveBeenCalledWith(G, 11, 'a'.repeat(40))
  })

  it('a merge with the switch ON that reached the remote leaves no follow-up Push', async () => {
    const t = setup({ merge: manualMergeView({ aheadOfTarget: 1 }) })
    await openMerge(t)
    await toggleSwitch()
    t.wt.db.results = mergeResults({ results: [mergeResultRow({ pushed: true, pushAvailable: false, pushBlockedReason: 'already_published' })] })
    await click('git-apply-merge')
    await confirmDialog()
    expect(t.wt.source.manualMerge).toHaveBeenCalledWith(G, 'v0.2', true)
    expect(q('git-result-push')).toBeNull()
  })

  it('a waiting merge behind the group Git job shows the part with a disabled Push and its reason', async () => {
    const t = setup({ results: mergeResults({ results: [mergeResultRow({ pushAvailable: false, pushBlockedReason: 'group_blocked' })] }) })
    await openMerge(t)
    expect(q('git-result-push')?.dataset.mode).toBe('blocked')
    expect(disabled('git-result-push-button')).toBe(true)
    expect(text('git-result-push-reason')).toBe('진행 중인 Git 작업 뒤에 Push 가능')
  })

  it('followUpPushMode: one / many push-available rows, group_blocked, else nothing', () => {
    const rows = (...reasons: Array<string | null>) => ({
      results: reasons.map((reason, i) => mergeResultRow({ mergeId: i + 1, pushAvailable: reason === null, pushBlockedReason: reason })),
    })
    expect(followUpPushMode(null, 0)).toBeNull()
    expect(followUpPushMode(rows(), 0)).toBeNull()
    expect(followUpPushMode(rows(null), 1)).toBe('one')
    expect(followUpPushMode(rows(null, null, 'already_published'), 2)).toBe('many')
    expect(followUpPushMode(rows('group_blocked', 'unmeasured'), 0)).toBe('blocked')
    expect(followUpPushMode(rows('unmeasured'), 0)).toBeNull()
    expect(followUpPushMode(rows('already_published', 'target_moved'), 0)).toBeNull()
  })
})

describe('Base 반영 — the existing judgment and rules stay (R §5-5)', () => {
  it('read-only: 머지 and Fetch off with 읽기 전용; the switch still shows its state', async () => {
    const t = setup({ results: mergeResults({ results: [mergeResultRow()] }) }, { readOnly: true })
    await openMerge(t)
    expect([disabled('git-apply-merge'), disabled('git-apply-fetch'), disabled('git-result-push-button')]).toEqual([true, true, true])
    expect(text('git-apply-reason')).toBe('읽기 전용')
    expect(pushSwitch().checked).toBe(false)
  })

  it('uncommitted changes block the merge with their one line', async () => {
    const t = setup({
      merge: manualMergeView({
        canMerge: false,
        blocked: [{ code: 'uncommitted_changes', reason: null, details: noDetails({ count: 2 }) }],
        uncommitted: { blocking: 2, counts: { unstaged: 2 }, protectedUncommitted: 0, reason: null },
      }),
    })
    await openMerge(t)
    expect(disabled('git-apply-merge')).toBe(true)
    expect(text('git-apply-reason')).toBe('미커밋 변경 2개 — 먼저 커밋하거나 되돌리세요')
  })
})
