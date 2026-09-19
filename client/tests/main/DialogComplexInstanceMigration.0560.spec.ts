/**
 * flowgate.default.0560 T0022 (5순위) — the complex instances this step moved onto the
 * common dialog layer, and the two it was told to leave alone.
 *
 * NR0005 §13's 5순위 is "AI/workflow 계열" + "Git review/conflict 계열". Four of the eleven
 * candidates were already done (T0016 took `WorkflowDecisionModal` and `GitBaseDirtyDialog`,
 * T0020 took the reject sub-dialog) and one — `GitConflictResolverDialog` — was carved out by
 * NR0005 §13 itself as "별도 하위 작업" and moved in T0024 instead. All nine are asserted below
 * against the same contract; only the step that performed each one differs.
 *
 * Where a behaviour already has a home, this spec does not duplicate it and says where to look:
 *   - the review/rerun single button and the loop panels → `tests/main/AiInvokeDialog.spec.ts`
 *   - the four continuous-work tabs                      → `tests/main/ContinuousWorkDialog.spec.ts`
 *   - the proposal dialog's two sections and its notice   → `tests/main/WorkPlanProposalDialog.0405.spec.ts`
 *   - approve/reject end to end + the nested pair         → `tests/main/GitMergeReviewDialog.spec.ts`,
 *                                                           `tests/main/DialogNestedInstanceSplit.0560.spec.ts`
 *   - backdrop-click policy across every dialog           → `tests/main/DialogOverlayNoClickSelf.0412.spec.ts`
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@shared/api', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  getRequest,
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

import GitUntrackedConflictDialog from '@main/components/GitUntrackedConflictDialog.vue'
import GroupChangesDialog from '@main/components/GroupChangesDialog.vue'
import NextActionModal from '@main/components/NextActionModal.vue'
import WorkPlanCreateDialog from '@main/components/WorkPlanCreateDialog.vue'
import WorkPlanProposalDialog from '@main/components/WorkPlanProposalDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'
import { useDocTypeStore } from '@main/stores/docTypeStore'
import { useAiProviderStore } from '@main/stores/aiProvider'

const CLIENT_DIR = resolve(__dirname, '../..')

function source(relative: string): string {
  return readFileSync(resolve(CLIENT_DIR, relative), 'utf8')
}

/* ───────────────────────── 1. the eight instances, in the source ───────────────────────── */

/** file → the variant D0008 §6 assigned it. */
const MIGRATED = [
  ['src/main/components/AiInvokeDialog.vue', 'workflow-large'],
  ['src/main/components/ContinuousWorkDialog.vue', 'workflow-large'],
  ['src/main/components/WorkPlanCreateDialog.vue', 'workflow-large'],
  ['src/main/components/WorkPlanProposalDialog.vue', 'workflow-large'],
  ['src/main/components/NextActionModal.vue', 'workflow-large'],
  ['src/main/components/GitUntrackedConflictDialog.vue', 'form-actions'],
  ['src/main/components/GroupChangesDialog.vue', 'workflow-large'],
  ['src/main/components/GitMergeReviewDialog.vue', 'workflow-large'],
  // 0560 T0024 (NR0005 §11.1 의 별도 하위 작업): the exception T0022 §3 listed here as
  // untouched. D0008 §6 had already assigned it `conflict-large`; the same three assertions
  // apply to it as to the other eight.
  ['src/main/components/GitConflictResolverDialog.vue', 'conflict-large'],
] as const

/**
 * Class ATTRIBUTES only. Prose in a comment must stay free to name the markup it replaced —
 * every migrated file above explains what it dropped, in those words.
 */
const LEGACY_CLASS = new RegExp(
  'class="[^"]*\\b(modal-bg|modal-box|modal-hd|modal-bd|modal-ft|modal-close|modal-title)\\b',
)

describe('T0022 §4-1 / T0024 §4-1 — the nine instances are on the common layer', () => {
  for (const [file, variant] of MIGRATED) {
    it(`${file.split('/').pop()} renders ${variant} with no hand-built shell left`, () => {
      const text = source(file)
      expect(text.match(LEGACY_CLASS), `legacy overlay markup is still in ${file}`).toBeNull()
      // Its own wrapper only: DialogShell does the teleporting now.
      expect(text, `${file} still teleports by hand`).not.toContain('<teleport to="body">')
      expect(text).toContain('<DialogShell')
      expect(text).toContain(`variant="${variant}"`)
    })
  }

  /**
   * §4-2. None of the eight overrides `closeOnBackdrop`, because none of them needs to: all
   * eight had a backdrop with no click handler, and both target variants already default to
   * `false`. An override here would be the default written twice — and, worse, the place a
   * later edit could quietly flip it.
   */
  it('§4-2 — not one of the nine overrides closeOnBackdrop', () => {
    for (const [file] of MIGRATED) {
      expect(source(file), `${file} overrides closeOnBackdrop`).not.toMatch(/:?close-on-backdrop=/)
    }
  })

  /**
   * §4-3 / §2.3 / §2.4. Both work-plan dialogs used to hold focus themselves so that an
   * element-bound `@keydown.escape` would fire at all. The stack's single document listener is
   * focus-independent, so the whole pattern goes — keeping it would mean two handlers for one
   * key press.
   */
  it('§4-3 — the two work-plan dialogs no longer hand-wire ESC or initial focus', () => {
    for (const file of [
      'src/main/components/WorkPlanCreateDialog.vue',
      'src/main/components/WorkPlanProposalDialog.vue',
    ]) {
      const text = source(file)
      expect(text, `${file} still binds ESC by hand`).not.toMatch(/@keydown\.escape/)
      expect(text, `${file} still makes its overlay focusable`).not.toMatch(/tabindex="-1"/)
      expect(text, `${file} still holds an overlayRef`).not.toMatch(/ref="overlayRef"/)
      expect(text, `${file} still focuses on a timer`).not.toMatch(/setTimeout\([^)]*focus/)
    }
  })

  /**
   * §4-10. 2순위 (T0014/TR0015) replaced every native `window.confirm()`; this step only had to
   * confirm that, because a leftover native popup cannot be a nested dialog above a
   * `workflow-large` stack member.
   */
  it('§4-10 — ContinuousWorkDialog asks through the common confirm, not window.confirm', () => {
    const text = source('src/main/components/ContinuousWorkDialog.vue')
    expect(text).not.toMatch(/window\.confirm\(/)
    expect(text).toContain("import { confirm } from '../composables/useDialogStack'")
    // The two revert paths T0014/TR0015 converted, counted at their call sites (the prose
    // above them names the same call and must stay free to).
    expect(text.match(/if \(!await confirm\(/g) ?? []).toHaveLength(2)
  })
})

/* ──────────────────── 2. what this step was told NOT to touch (§3) ──────────────────── */

describe('T0022 §3 — the explicit exclusions', () => {
  /**
   * 0560 T0024 §2.8-1: this case used to assert the opposite — `git-conflict-overlay` present,
   * no `<DialogShell>` — because NR0005 §13 carved the resolver out of 5순위. It was carved out
   * to be its own sub-task, not to stay behind, and T0024 performed it: the file is in
   * `MIGRATED` above now. What survives here is the part of the exclusion that is still true —
   * the dialog's own body markup is NOT common (D0008 §6), so the classes T0022 would have had
   * to delete for a full migration are all still in the file.
   */
  it('GitConflictResolverDialog kept its body when it moved (D0008 §6)', () => {
    const text = source('src/main/components/GitConflictResolverDialog.vue')
    for (const owned of [
      'git-conflict-body',
      'git-conflict-dialog-bd',
      'git-ai-assist-strip',
      'git-conflict-ai-strip',
      'git-conflict-message-bar',
      'git-conflict-footer-context',
    ]) {
      expect(text, `${owned} is no longer feature-owned`).toContain(`class="${owned}`)
    }
  })

  it('WorkflowDecisionModal and GitBaseDirtyDialog were already done in 3순위', () => {
    for (const [file, variant] of [
      ['src/main/components/WorkflowDecisionModal.vue', 'workflow-large'],
      ['src/main/components/GitBaseDirtyDialog.vue', 'form-actions'],
    ] as const) {
      const text = source(file)
      expect(text).toContain(`variant="${variant}"`)
      expect(text.match(LEGACY_CLASS), `${file} was touched`).toBeNull()
    }
  })

  it('GitMergeRejectDialog is still mounted as a sibling of the parent shell, unchanged', () => {
    const review = source('src/main/components/GitMergeReviewDialog.vue')
    // Outside `</DialogShell>`: the child teleports itself, and nesting it inside the parent's
    // slots would tie its lifetime to a slot rather than to `rejectPromptOpen`.
    const closing = review.indexOf('</DialogShell>')
    expect(closing).toBeGreaterThan(0)
    expect(review.indexOf('<GitMergeRejectDialog')).toBeGreaterThan(closing)
  })
})

/* ───────────────────────────── 3. behaviour, per instance ───────────────────────────── */

const wrappers: VueWrapper[] = []

function track<T extends VueWrapper>(wrapper: T): T {
  wrappers.push(wrapper)
  return wrapper
}

function mountOptions(extra: Record<string, unknown> = {}) {
  return { global: { plugins: [i18n] }, attachTo: document.body, ...extra }
}

function surface(): HTMLElement | null {
  return document.body.querySelector<HTMLElement>('.fg-dialog-surface')
}

function actionIds(): string[] {
  return [...document.body.querySelectorAll('.fg-dialog-footer [data-dialog-action-id]')]
    .map((el) => el.getAttribute('data-dialog-action-id') ?? '')
}

function roles(): string[] {
  return [...document.body.querySelectorAll('.fg-dialog-footer [data-dialog-action-role]')]
    .map((el) => el.getAttribute('data-dialog-action-role') ?? '')
}

function action(id: string): HTMLButtonElement {
  const button = document.body.querySelector<HTMLButtonElement>(`[data-dialog-action-id="${id}"]`)
  if (button == null) throw new Error(`footer action "${id}" is not rendered`)
  return button
}

function headerClose(): HTMLButtonElement {
  const button = document.body.querySelector<HTMLButtonElement>('.fg-dialog-header__close')
  if (button == null) throw new Error('the dialog header has no close button')
  return button
}

function clickBackdrop(): void {
  const el = document.body.querySelector<HTMLElement>('.fg-dialog-overlay')
  if (el == null) throw new Error('no dialog is open')
  el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
  el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
}

function pressEscape(): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  document.body.innerHTML = ''
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockResolvedValue({ data: {} })
  postRequest.mockResolvedValue({ data: {} })
})

afterEach(() => {
  while (wrappers.length > 0) wrappers.pop()!.unmount()
  resetDialogSystem()
  document.body.innerHTML = ''
})

/* — §2.6 / §4-9: GitUntrackedConflictDialog's four-button footer — */

describe('GitUntrackedConflictDialog (form-actions)', () => {
  async function open(scope: 'base' | 'group', blockers?: { untrackedFiles?: string[]; trackedFiles?: string[] }) {
    const wrapper = track(mount(GitUntrackedConflictDialog, mountOptions()))
    const outcome = (wrapper.vm as never as {
      resolve: (id: string, files: string[], scope?: string, groups?: unknown) => Promise<string>
    }).resolve('flowgate.default.0350', ['blocked.txt'], scope, blockers)
    await flushPromises()
    return { wrapper, outcome }
  }

  it('§4-9 — group scope draws four actions in the contract order, base scope three', async () => {
    await open('group', { untrackedFiles: ['blocked.txt'], trackedFiles: ['tracked.txt'] })
    // DS0007's order, produced by role priority rather than by DOM order: 보조 → 위험 →
    // 취소 → 주버튼. The old row was [취소] [삭제] [되돌리기] [커밋].
    expect(actionIds()).toEqual(['revert', 'remove', 'cancel', 'commit'])
    expect(roles()).toEqual(['aux', 'danger', 'cancel', 'primary'])
    expect(action('remove').classList.contains('fg-dialog-btn--tone-danger')).toBe(true)

    while (wrappers.length > 0) wrappers.pop()!.unmount()
    resetDialogSystem()
    document.body.innerHTML = ''

    await open('base')
    expect(actionIds()).toEqual(['remove', 'cancel', 'commit'])
  })

  it('§4-9 — the disabled conditions are the ones the inline buttons carried', async () => {
    await open('group', { untrackedFiles: [], trackedFiles: [] })
    // Nothing to act on in either set: only [취소] stays live.
    expect(action('remove').disabled).toBe(true)
    expect(action('commit').disabled).toBe(true)
    expect(action('revert').disabled).toBe(true)
    expect(action('cancel').disabled).toBe(false)
  })

  it('§4-2 — the backdrop still does not close it, and [취소] resolves cancel', async () => {
    const { outcome } = await open('base')
    clickBackdrop()
    await flushPromises()
    expect(surface()).not.toBeNull()

    action('cancel').click()
    await flushPromises()
    expect(await outcome).toBe('cancel')
    expect(postRequest).not.toHaveBeenCalled()
  })
})

/* — §2.7 / §4-7: GroupChangesDialog has no visible prop of its own — */

describe('GroupChangesDialog (workflow-large)', () => {
  function open() {
    return track(mount(GroupChangesDialog, mountOptions({
      props: {
        projectId: 'flowgate',
        groupId: 'flowgate.default.0325',
        branch: 'flowgate_default_0325',
        baseBranch: 'main',
        changes: [{ path: 'a.py', status: 'M', insertions: 1, deletions: 0 }],
      },
    })))
  }

  it('§4-7 — mounting IS opening: no open/visible prop was invented for it', async () => {
    const text = source('src/main/components/GroupChangesDialog.vue')
    expect(text).toContain(':open="true"')
    expect(text).not.toMatch(/visible\??:/)

    const wrapper = open()
    await flushPromises()
    expect(surface()).not.toBeNull()
    wrapper.unmount()
    wrappers.pop()
    // Unmounting takes the whole dialog with it — the shell's force_cleanup path.
    expect(surface()).toBeNull()
  })

  it('has one cancel-role action, and all three exits answer close exactly once', async () => {
    const wrapper = open()
    await flushPromises()
    expect(actionIds()).toEqual(['back'])
    expect(roles()).toEqual(['cancel'])

    action('back').click()
    await flushPromises()
    headerClose().click()
    await flushPromises()
    pressEscape()
    await flushPromises()
    expect(wrapper.emitted('close')).toHaveLength(3)

    clickBackdrop()
    await flushPromises()
    expect(wrapper.emitted('close')).toHaveLength(3)
  })
})

/* — §2.3 / §4-3: WorkPlanCreateDialog's ESC and initial focus — */

describe('WorkPlanCreateDialog (workflow-large)', () => {
  function open() {
    getRequest.mockImplementation((url: string) => {
      if (String(url).includes('/document-types')) {
        return Promise.resolve({ data: { data: [], work_plan_countable_types: [] } })
      }
      return Promise.resolve({ data: { ok: true, project: 'flowgate', providers: [], default_provider_id: null } })
    })
    return track(mount(WorkPlanCreateDialog, mountOptions({
      props: {
        visible: true,
        parentDocId: 'flowgate.default.0402.0001-R',
        projectId: 'flowgate',
        groupId: 'flowgate.default.0402',
      },
    })))
  }

  it('§4-3 — ESC still closes it, now through the stack', async () => {
    const wrapper = open()
    await flushPromises()
    pressEscape()
    await flushPromises()
    expect(wrapper.emitted('update:visible')![0]).toEqual([false])
  })

  /**
   * §4-3's other half, told the way the code actually tells it.
   *
   * The old overlay was a focusable `div[tabindex="-1"]` that grabbed focus 50ms after open -
   * NOT an input - and it did that for one reason: an element-bound `@keydown.escape` only
   * fires while that element holds focus. So what had to survive the migration is the RESULT
   * (ESC closes; the keyboard stays inside the dialog), not the mechanism.
   *
   * flowgate.default.0560 TR0023 rejection rework: L0009 §4's tree resolves autofocus ->
   * primary -> cancel, and the primary [만들기] button is `disabled` on a cold open (nothing is
   * picked yet) — calling `.focus()` on a disabled button is a no-op, so leaving the tree to
   * land on `primary` would put focus nowhere inside the dialog. `[전체선택]` in §1 now carries
   * `data-dialog-autofocus` and is never disabled, so it is the target the tree actually picks,
   * and the first Tab is still pulled back inside by the shell's focus trap if it ever escapes.
   */
  it('§4-3 — initial focus lands on the enabled [전체선택] target, not the disabled primary', async () => {
    open()
    await flushPromises()

    const overlay = document.body.querySelector<HTMLElement>('.fg-dialog-overlay')!
    expect(overlay.getAttribute('tabindex')).toBeNull()
    expect(document.activeElement).not.toBe(overlay)

    // The primary [만들기] button is disabled on a cold open — nothing picked yet.
    const primary = surface()!.querySelector<HTMLButtonElement>('[data-dialog-action-role="primary"]')
    expect(primary?.disabled).toBe(true)

    // The intentional initial-focus target instead: the enabled [전체선택] button.
    const autofocusTarget = surface()!.querySelector('[data-dialog-autofocus]')
    expect(autofocusTarget).not.toBeNull()
    expect(document.activeElement).toBe(autofocusTarget)

    // Focus landing outside is still pulled back in by the shell's focus trap.
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    outside.focus()
    outside.dispatchEvent(new FocusEvent('focusin', { bubbles: true }))
    await flushPromises()
    expect(surface()!.contains(document.activeElement)).toBe(true)
    outside.remove()
  })

  it('§4-2 — the backdrop does not close it; the footer is [취소] then [만들기]', async () => {
    const wrapper = open()
    await flushPromises()
    expect(actionIds()).toEqual(['cancel', 'create'])
    expect(roles()).toEqual(['cancel', 'primary'])

    clickBackdrop()
    await flushPromises()
    expect(wrapper.emitted('update:visible')).toBeFalsy()
  })
})

/* — §2.4 / §4-3: WorkPlanProposalDialog gets the same enabled initial-focus target — */

describe('WorkPlanProposalDialog (workflow-large)', () => {
  function open() {
    const docTypeStore = useDocTypeStore()
    docTypeStore.items = [
      { id: 1, code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet' },
    ] as any
    docTypeStore.labelMap = { D: '기본설계' }
    docTypeStore.workPlanCountableTypes = [
      { code: 'D', label: '기본설계', category: 'design', unit: 'sheet' },
    ] as any
    const aiProviderStore = useAiProviderStore()
    aiProviderStore.providers = [{ id: 'aip_x', name: 'X', kind: 'claude', exec_type: 'cli' }] as any
    // Seeding `loadedProjectId` before mount is what makes `providersSettled` resolve
    // synchronously — the dialog's body (and its footer's primary action) is already in the
    // DOM by the time `DialogShell` applies its cold-open focus resolution, exactly as it would
    // be for a project whose provider list was already fetched before this dialog opened.
    aiProviderStore.loadedProjectId = 'flowgate'
    return track(mount(WorkPlanProposalDialog, mountOptions({
      props: {
        visible: true,
        parentDocId: 'flowgate.default.0405.0001-R',
        projectId: 'flowgate',
        groupId: 'flowgate.default.0405',
      },
    })))
  }

  /**
   * flowgate.default.0560 TR0023 rejection rework: [AI 호출]/[+문서생성] used to both stay
   * disabled until a type was picked, so the tree would otherwise nominate a disabled primary
   * button on a cold open. `[전체선택]` in §1 carries `data-dialog-autofocus` here too.
   *
   * flowgate.default.0591 T0005 relaxed [+문서생성]'s own condition — in the no-provider branch
   * exercised here it is `role="primary"` and no longer needs a type picked, so it is enabled
   * from the very first render. That does not change the invariant this test exists for:
   * `resolveInitialFocus()` always prefers an explicit `[data-dialog-autofocus]` target over
   * `primary` regardless of the primary's disabled state (D0008/L0009 §4), so [전체선택] still
   * gets the focus either way.
   */
  it('§4-3 — initial focus lands on the explicit [전체선택] target, not the primary button', async () => {
    open()
    await flushPromises()

    const autofocusTarget = surface()!.querySelector('[data-dialog-autofocus]')
    expect(autofocusTarget).not.toBeNull()
    expect(document.activeElement).toBe(autofocusTarget)

    const primary = surface()!.querySelector<HTMLButtonElement>('[data-dialog-action-role="primary"]')
    expect(primary).not.toBeNull()
    expect(autofocusTarget).not.toBe(primary)
  })
})

/* — §2.5 / §4-4: NextActionModal's split [진행] trigger — */

describe('NextActionModal (workflow-large)', () => {
  async function open() {
    getRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v1/modules') return { data: { items: [{ module_id: 'default', title: 'default' }] } }
      if (/\/groups$/.test(path)) {
        return { data: { ok: true, total: 0, offset: 0, limit: 100, items: [] } }
      }
      if (/\/documents$/.test(path)) return { data: { items: [] } }
      if (/\/predecessors$/.test(path)) return { data: { predecessor_doc_ids: [] } }
      return { data: {} }
    })
    const wrapper = track(mount(NextActionModal, mountOptions({
      props: {
        visible: true,
        nextStepLabel: 'TR',
        nextTypeCode: 'TR',
        projectId: 'flowgate',
        docModule: 'default',
        groupId: 'flowgate.default.0412',
      },
    })))
    await flushPromises()
    await flushPromises()
    return wrapper
  }

  it('§4-4 — the trigger is a real DialogAction and the panel is its popover sibling', async () => {
    await open()
    expect(actionIds()).toEqual(['cancel', 'proceed'])
    expect(roles()).toEqual(['cancel', 'primary'])

    // The panel cannot live inside the `<button>` — five interactive items would be nested in
    // one. `popover-proceed` renders it as the action's sibling in the anchor box.
    const panel = document.body.querySelector('.nad-proceed-dropdown')!
    expect(panel).not.toBeNull()
    expect(action('proceed').contains(panel)).toBe(false)
    expect(panel.closest('.fg-dialog-footer__action')).not.toBeNull()
    expect(panel.querySelectorAll('.nad-proceed-item')).toHaveLength(5)
  })

  it('§4-4 — the trigger toggles the panel and an item runs its action', async () => {
    const wrapper = await open()
    const panel = () => document.body.querySelector('.nad-proceed-dropdown')!

    expect(panel().classList.contains('open')).toBe(false)
    action('proceed').click()
    await flushPromises()
    expect(panel().classList.contains('open')).toBe(true)
    action('proceed').click()
    await flushPromises()
    expect(panel().classList.contains('open')).toBe(false)

    action('proceed').click()
    await flushPromises()
    ;(panel().querySelectorAll<HTMLElement>('.nad-proceed-item')[1]).click()
    await flushPromises()
    expect(wrapper.emitted('copy-mention')).toHaveLength(1)
    expect(wrapper.emitted('update:visible')![0]).toEqual([false])
  })
})
