/**
 * flowgate.default.0560 T0020 (4.5순위) — the four components that held more than one dialog
 * instance, or held one nested inside another.
 *
 * NR0005 §13's last line is the whole point of this batch: "이 그룹은 파일 하나를 옮기는
 * 문제가 아니라 내부 dialog instance를 분해해야 한다". Two of the four (`GitActionMenu`,
 * `NotificationCenter`) were already ON the common layer after T0018 — their shell block was
 * simply still inside the host file, so the work here is pure extraction and the risk is a
 * silent behaviour change. The other two are real splits: `AiProviderListEditor` carried three
 * dialogs in legacy `.modal-bg` markup, and `GitMergeReviewDialog` carried a hand-rolled
 * nested overlay.
 *
 * Where a behaviour already has a home, this spec does not duplicate it and says where to look:
 *   - AiProvider backdrop/ESC behaviour   → `tests/settings.ai-provider-errors.spec.ts`
 *   - the magic tool inside the form      → `tests/settings.ai-magic-tool.0519.spec.ts`
 *   - the reject flow end to end + the parent guard in action
 *                                         → `tests/main/GitMergeReviewDialog.spec.ts`
 *   - which file hosts which shell        → `tests/main/DialogViewInstanceMigration.0560.spec.ts`
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
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

import AiProviderListEditor from '../../src/settings/components/AiProviderListEditor.vue'
import ConfirmDialog from '@main/components/dialogs/ConfirmDialog.vue'
import GitMergeRejectDialog from '@main/components/GitMergeRejectDialog.vue'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'
import GitStatusPanelDialog from '@main/components/GitStatusPanelDialog.vue'
import NotificationAiDetailDialog from '@main/components/NotificationAiDetailDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'

const CLIENT_DIR = resolve(__dirname, '../..')

function source(relative: string): string {
  return readFileSync(resolve(CLIENT_DIR, relative), 'utf8')
}

/* ─────────────────── 1. the split is real, in the source (T0020 §4) ─────────────────── */

const NEW_COMPONENTS = [
  ['src/settings/components/AiProviderFormDialog.vue', 'form'],
  ['src/settings/components/AiProviderCommandInfoDialog.vue', 'readonly'],
  ['src/settings/components/AiProviderDeleteConfirmDialog.vue', 'confirm-danger'],
  ['src/main/components/GitMergeRejectDialog.vue', 'form-actions'],
  ['src/main/components/NotificationAiDetailDialog.vue', 'readonly'],
  ['src/main/components/GitStatusPanelDialog.vue', 'readonly'],
] as const

/** Class ATTRIBUTES only: prose in a comment must be free to name the markup it replaced. */
const LEGACY_CLASS = new RegExp(
  'class="[^"]*\\b(modal-bg|modal-box|modal-hd|modal-bd|modal-ft|modal-close|modal-title|modal)\\b',
)

describe('AiProviderListEditor is three dialog components now (T0020 §4-1)', () => {
  it('has no legacy overlay markup and no shell of its own left', () => {
    const editor = source('src/settings/components/AiProviderListEditor.vue')
    expect(editor.match(LEGACY_CLASS), 'legacy overlay markup is still in the editor').toBeNull()
    expect(editor).not.toContain('<DialogShell')
    // What D0008 §4 leaves with the parent: open state and the data handed down.
    for (const tag of ['<AiProviderFormDialog', '<AiProviderCommandInfoDialog', '<AiProviderDeleteConfirmDialog']) {
      expect(editor, `the editor no longer mounts ${tag}`).toContain(tag)
    }
    expect(editor).toContain('const formOpen = ref(false)')
    expect(editor).toContain('const cmdIndex = ref(null)')
    expect(editor).toContain('const deleteIndex = ref(null)')
  })

  it.each(NEW_COMPONENTS)('%s renders the common shell as `%s`', (file, variant) => {
    const text = source(file)
    expect(text).toContain('<DialogShell')
    expect(text).toContain('<DialogHeader')
    expect(text).toContain(`variant="${variant}"`)
    expect(text.match(LEGACY_CLASS), `${file} carries legacy overlay markup`).toBeNull()
  })
})

/**
 * T0020 §4-2. All three of the AiProvider instances already behaved the way their target
 * variant's default behaves, so an override would only restate the default — the opposite of
 * the 4순위 batch, where 0412 T0004's backdrop-no-close contract disagreed with `readonly`'s
 * default and had to be written down. Stated as a test so a later "for consistency" override
 * has to justify itself.
 */
describe('the AiProvider dialogs lean on the variant defaults (T0020 §4-2)', () => {
  it.each([
    ['src/settings/components/AiProviderFormDialog.vue'],
    ['src/settings/components/AiProviderCommandInfoDialog.vue'],
    ['src/settings/components/AiProviderDeleteConfirmDialog.vue'],
    ['src/main/components/GitMergeRejectDialog.vue'],
  ])('%s passes no close-on-backdrop override', (file) => {
    expect(source(file)).not.toContain('close-on-backdrop')
  })
})

/**
 * T0020 §4-3. The overlays used to hold focus themselves (`tabindex="-1"` + `focus()`) purely
 * so an element-bound `@keydown.esc` could fire. The stack's single document listener judges
 * ESC regardless of focus (L0009 §2), so keeping the old trio would mean two handlers for one
 * key. The ESC-still-closes half of this is in settings.ai-provider-errors.spec.ts.
 */
describe('the manual ESC/focus plumbing is gone (T0020 §4-3)', () => {
  it.each([
    ['src/settings/components/AiProviderListEditor.vue'],
    ['src/settings/components/AiProviderFormDialog.vue'],
    ['src/settings/components/AiProviderCommandInfoDialog.vue'],
    ['src/settings/components/AiProviderDeleteConfirmDialog.vue'],
  ])('%s binds no keydown.esc and focuses no overlay by hand', (file) => {
    const text = source(file)
    expect(text).not.toContain('keydown.esc')
    expect(text).not.toContain('tabindex="-1"')
    expect(text).not.toContain('Overlay.value?.focus')
    expect(text).not.toContain('restoreTriggerFocus')
  })
})

/* ─────────────── 2. the nested pair (T0020 §2.2 / §4-5 · §4-6) ─────────────── */

describe('GitMergeReviewDialog keeps only the parent half (T0020 §4-5)', () => {
  const review = source('src/main/components/GitMergeReviewDialog.vue')

  it('no longer renders the reject overlay or its CSS', () => {
    expect(review).not.toContain('gmr-reject-overlay')
    expect(review).not.toContain('gmr-reject-box')
    expect(review).not.toContain('gmr-reject-actions')
    expect(review).toContain('<GitMergeRejectDialog')
  })

  /**
   * The parent's own shell is NOT this T's business — NR0005 §13 5순위 moves it together with
   * `GroupChangesDialog`. Asserted here so "while I was in the file" cannot quietly take it.
   */
  it('still opens its own teleport and legacy modal box, untouched', () => {
    expect(review).toContain('<teleport to="body">')
    expect(review).toContain('class="modal-bg"')
    expect(review).toContain('document-modal document-modal--edit gmr-modal')
  })

  /**
   * T0020 §2.2 / §4-6: the approximation of L0009's "child active 중 parent: interaction
   * 비활성". The real contract needs the parent on the stack, which 5순위 does; until then the
   * three controls that act on the review are disabled by hand while the child is up. The
   * rendered behaviour is exercised in tests/main/GitMergeReviewDialog.spec.ts.
   */
  it('disables approve, reject and the header X while the sub-dialog is open', () => {
    expect(review).toContain('"busy || !canApprove || rejectPromptOpen"')
    expect(review).toContain('"busy || !canReject || rejectPromptOpen"')
    expect(review).toMatch(/class="modal-close"[^>]*:disabled="rejectPromptOpen"/)
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

function roles(): string[] {
  return [...document.body.querySelectorAll('.fg-dialog-footer [data-dialog-action-role]')]
    .map((el) => el.getAttribute('data-dialog-action-role') ?? '')
}

function actionIds(): string[] {
  return [...document.body.querySelectorAll('.fg-dialog-footer [data-dialog-action-id]')]
    .map((el) => el.getAttribute('data-dialog-action-id') ?? '')
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
  i18n.global.locale.value = 'ko'
  document.body.innerHTML = ''
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockResolvedValue({ data: {} })
  postRequest.mockResolvedValue({ data: {} })
})

afterEach(() => {
  while (wrappers.length > 0) {
    try {
      wrappers.pop()?.unmount()
    } catch {
      // already unmounted
    }
  }
  resetDialogSystem()
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

/* — AiProviderListEditor's three — */

describe('the three AiProvider dialogs as they render', () => {
  const CATALOG = { exec_types: ['cli', 'api'], kinds: { cli: ['claude'], api: ['claude'] } }
  const ROW = {
    id: 'aip_1', name: 'claude cli', exec_type: 'cli', kind: 'claude',
    enabled: true, cli_command: 'claude -p',
  }

  function mountEditor() {
    return track(mount(AiProviderListEditor as never, mountOptions({
      props: { providers: [ROW], defaultIndex: 0, catalog: CATALOG },
    })))
  }

  async function openRowAction(wrapper: VueWrapper, title: string) {
    await wrapper.find(`button[title="${i18n.global.t(title)}"]`).trigger('click')
    await flushPromises()
  }

  it('paints [취소] [저장] on the form and starts focus in the name field', async () => {
    const wrapper = mountEditor()
    await openRowAction(wrapper, 'common.edit')

    expect(surface()?.getAttribute('data-dialog-variant')).toBe('form')
    expect(roles()).toEqual(['cancel', 'primary'])
    expect(actionIds()).toEqual(['cancel', 'save'])
    // Without `data-dialog-autofocus` L0009 §4 would start on the primary button; the old
    // dialog focused the name input by hand, and that is what carries over.
    expect(document.activeElement).toBe(document.body.querySelector('.fg-dialog-body input.form-ctrl'))
  })

  it('gives the command view one dismiss button and no primary', async () => {
    const wrapper = mountEditor()
    await openRowAction(wrapper, 'settings.ai.view_command')

    expect(surface()?.getAttribute('data-dialog-variant')).toBe('readonly')
    expect(roles()).toEqual(['dismiss'])
    expect(actionIds()).toEqual(['close'])
    expect(document.body.querySelector('[data-dialog-action-role="primary"]')).toBeNull()
  })

  /**
   * T0020 §4-4. D0008 §6's Danger Confirm is `[취소] [위험 주버튼]`: the destructive action is
   * the one that completes this dialog, so it takes the PRIMARY position and `tone="danger"`
   * paints it — `role: 'danger'` is the other thing, a destructive action sitting beside the
   * button that completes the dialog, which `footerRolePriority` puts LEFT of cancel.
   */
  it('paints the delete confirm as [취소] [삭제], danger-toned primary', async () => {
    const wrapper = mountEditor()
    await openRowAction(wrapper, 'common.delete')

    expect(surface()?.getAttribute('data-dialog-variant')).toBe('confirm-danger')
    expect(roles()).toEqual(['cancel', 'primary'])
    expect(actionIds()).toEqual(['cancel', 'delete'])
    expect(action('delete').className).toContain('fg-dialog-btn--tone-danger')
    expect(action('delete').className).not.toContain('fg-dialog-btn--danger')
    // L0009 §4 sends confirm-danger's initial focus to the safer button.
    expect(document.activeElement).toBe(action('cancel'))
  })

  it('deletes the row through the parent, which still owns the list', async () => {
    const wrapper = mountEditor()
    await openRowAction(wrapper, 'common.delete')

    action('delete').click()
    await flushPromises()

    expect(wrapper.emitted('update:providers')).toHaveLength(1)
    expect(wrapper.emitted('update:providers')![0][0]).toEqual([])
    expect(surface()).toBeNull()
  })

  /**
   * T0020 §2.1. `restoreTriggerFocus()` — a `nextTick(() => lastTrigger?.focus?.())` run from
   * each of the three close functions — is gone, and `lastTrigger` became a ref handed down as
   * `return-focus-to`. Deleting a focus restore leaves no visible trace when it is wrong, so
   * each dialog is closed here and asked where focus actually went.
   */
  it.each([
    ['common.edit', () => action('cancel').click()],
    ['settings.ai.view_command', () => action('close').click()],
    ['common.delete', () => action('cancel').click()],
  ])('returns focus to the row button that opened %s', async (title, close) => {
    const wrapper = mountEditor()
    const trigger = wrapper.find(`button[title="${i18n.global.t(title)}"]`).element as HTMLElement
    await openRowAction(wrapper, title)
    expect(surface(), 'the dialog did not open').not.toBeNull()

    close()
    await flushPromises()

    expect(surface()).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it('returns focus to the row button when ESC closes the dialog', async () => {
    const wrapper = mountEditor()
    const trigger = wrapper
      .find(`button[title="${i18n.global.t('settings.ai.view_command')}"]`).element as HTMLElement
    await openRowAction(wrapper, 'settings.ai.view_command')

    pressEscape()
    await flushPromises()

    expect(surface()).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })
})

/* — GitMergeRejectDialog — */

describe('GitMergeRejectDialog (T0020 §2.2)', () => {
  function mountReject(props: Record<string, unknown> = {}) {
    return track(mount(GitMergeRejectDialog, mountOptions({
      props: {
        open: true,
        busy: false,
        providers: [{ id: 'p1', name: 'Claude Sonnet 5' }],
        selectedProvider: 'p1',
        providerLoading: false,
        providerErrored: false,
        ...props,
      },
    })))
  }

  function mountConfirmHost() {
    return track(mount(ConfirmDialog as never, mountOptions({ props: { host: true } })))
  }

  function reasonBox(): HTMLTextAreaElement {
    const el = document.body.querySelector<HTMLTextAreaElement>('.fg-dialog-body textarea')
    if (el == null) throw new Error('the reason field is not rendered')
    return el
  }

  async function type(text: string) {
    const box = reasonBox()
    box.value = text
    box.dispatchEvent(new Event('input'))
    await flushPromises()
  }

  it('paints [취소] [반려 확정] and gates the confirm exactly as the inline button did', async () => {
    mountReject()
    expect(surface()?.getAttribute('data-dialog-variant')).toBe('form-actions')
    expect(roles()).toEqual(['cancel', 'primary'])
    expect(actionIds()).toEqual(['cancel', 'reject-confirm'])
    expect(action('reject-confirm').className).toContain('fg-dialog-btn--tone-danger')
    // empty reason
    expect(action('reject-confirm').disabled).toBe(true)
    await type('please redo the yaml side')
    expect(action('reject-confirm').disabled).toBe(false)
  })

  it('disables the confirm when no provider is chosen', async () => {
    mountReject({ selectedProvider: '' })
    await type('reason')
    expect(action('reject-confirm').disabled).toBe(true)
  })

  it('starts focus in the reason field and reports the trimmed reason', async () => {
    const wrapper = mountReject()
    await flushPromises()
    expect(document.activeElement).toBe(reasonBox())

    await type('  please redo the yaml side  ')
    action('reject-confirm').click()
    await flushPromises()

    expect(wrapper.emitted('reject')).toHaveLength(1)
    expect(wrapper.emitted('reject')![0][0]).toBe('please redo the yaml side')
  })

  it('opens with an empty reason every time', async () => {
    const wrapper = mountReject({ open: false })
    await wrapper.setProps({ open: true })
    await flushPromises()
    expect(reasonBox().value).toBe('')

    await type('half-written instruction')
    await wrapper.setProps({ open: false })
    await wrapper.setProps({ open: true })
    await flushPromises()
    expect(reasonBox().value).toBe('')
  })

  it('closes without a question while the reason is still empty', async () => {
    mountConfirmHost()
    const wrapper = mountReject()

    pressEscape()
    await flushPromises()

    expect(document.body.querySelector('[data-dialog-variant="confirm-danger"]')).toBeNull()
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  /**
   * T0020 §2.2 option (a). ESC did not exist on this box before the migration — nothing in it
   * held focus and no handler was bound — so joining the stack created a new way to lose up to
   * 4000 characters of typed instruction. All three close paths give the same answer.
   */
  it.each([
    ['escape', () => pressEscape()],
    ['the header X', () => headerClose().click()],
    ['[취소]', () => action('cancel').click()],
  ])('asks before throwing away a typed reason closed by %s', async (_label, close) => {
    mountConfirmHost()
    const wrapper = mountReject()
    await type('please redo the yaml side')

    close()
    await flushPromises()

    const confirmSurface = document.body.querySelector('[data-dialog-variant="confirm-danger"]')
    expect(confirmSurface, 'the discard confirm never appeared').not.toBeNull()
    expect(wrapper.emitted('close')).toBeFalsy()

    // Answering "no" keeps the text.
    confirmSurface!.querySelector<HTMLElement>('[data-dialog-action-role="cancel"]')!.click()
    await flushPromises()
    expect(wrapper.emitted('close')).toBeFalsy()
    expect(reasonBox().value).toBe('please redo the yaml side')

    // Answering "yes" lets it go.
    close()
    await flushPromises()
    document.body
      .querySelector<HTMLElement>('[data-dialog-variant="confirm-danger"] [data-dialog-action-role="primary"]')!
      .click()
    await flushPromises()
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('stays put on a backdrop click', async () => {
    const wrapper = mountReject()
    clickBackdrop()
    await flushPromises()
    expect(wrapper.emitted('close')).toBeFalsy()
    expect(surface()).not.toBeNull()
  })
})

/**
 * The nested pair as the app assembles it — the real `GitMergeReviewDialog` with the real child
 * on top of it. The source checks above say the guard and the wiring are written; this says the
 * two dialogs behave as a pair once mounted, which is what T0020 §4-6 and §4-8 ask for.
 *
 * Note what jsdom can and cannot answer here. It can answer the stack's own z-index, because
 * DialogShell writes it inline; it CANNOT compare that with the parent's `.modal-bg` z-index,
 * which lives in `shared/app.css` and is never loaded. That comparison is a painted-pixel
 * question and it is measured under the production stylesheet by
 * `tests/browser/dialog-nested-split-geometry.0560.mjs`.
 */
describe('the nested pair, mounted for real (T0020 §4-6 · §4-8)', () => {
  const REVIEW = {
    ok: true,
    result: {
      group_id: 'flowgate.default.0481',
      merge_id: 9,
      review_state: 'resolved_pending_review',
      review_fingerprint: 'fp-1',
      instruction_generation: 0,
      base_head: 'base1',
      merge_head: 'merge1',
      changes: [{ path: 'server/app/git_service.py', status: 'M', old_path: null }],
      conflict_origins: [],
      conversation: [],
      held_test_operations: [],
      pending_conversation: null,
      resolver_provider: 'Claude Sonnet 5',
      reconciliation_kind: null,
      last_error: null,
      can_approve: true,
      can_reject: true,
      can_send: true,
    },
  }

  async function mountReview() {
    getRequest.mockImplementation((url: string) => {
      if (String(url).includes('/review-diff')) {
        return Promise.resolve({
          data: {
            ok: true,
            result: {
              path: 'server/app/git_service.py', status: 'M',
              old: { content: 'a\n', binary: false, truncated: false },
              new: { content: 'b\n', binary: false, truncated: false },
            },
          },
        })
      }
      return Promise.resolve({ data: REVIEW })
    })
    const wrapper = track(mount(GitMergeReviewDialog, mountOptions({
      props: {
        groupId: 'flowgate.default.0481',
        mergeId: 9,
        branch: 'group/0481',
        baseBranch: 'main',
        providers: [{ id: 'p1', name: 'Claude Sonnet 5' }],
        selectedProvider: 'p1',
      },
    })))
    await flushPromises()
    return wrapper
  }

  /** The parent teleports to `<body>`, so its own controls are not inside the wrapper. */
  function parentButton(selector: string): HTMLButtonElement {
    const el = document.body.querySelector<HTMLButtonElement>(selector)
    if (el == null) throw new Error(`the review dialog has no ${selector}`)
    return el
  }

  const rejectTrigger = () => parentButton('.gmr-ft-actions .btn-danger-ol')
  const reasonBox = () => document.body.querySelector<HTMLTextAreaElement>('.fg-dialog-body textarea')!

  async function openChild() {
    const trigger = rejectTrigger()
    trigger.click()
    await flushPromises()
    expect(
      document.body.querySelector('[data-dialog-variant="form-actions"]'),
      'the reject sub-dialog did not open',
    ).not.toBeNull()
    return trigger
  }

  it('opens the child above the parent and stacks the discard confirm above that', async () => {
    await mountReview()
    await openChild()

    // The parent is still there, DOM and state intact (L0009 §2 "child active 중 parent").
    expect(document.body.querySelector('.modal-bg .gmr-modal')).not.toBeNull()

    const child = document.body.querySelector<HTMLElement>('.fg-dialog-overlay')!
    expect(Number(child.style.zIndex)).toBe(1600)

    // A third layer on top: the discard confirm of §2.2 (a). One step further up, not level
    // with the dialog it is asking about.
    track(mount(ConfirmDialog as never, mountOptions({ props: { host: true } })))
    reasonBox().value = '다시 해 주세요'
    reasonBox().dispatchEvent(new Event('input'))
    await flushPromises()
    pressEscape()
    await flushPromises()

    const confirmOverlay = document.body
      .querySelector<HTMLElement>('[data-dialog-variant="confirm-danger"]')!
      .closest<HTMLElement>('.fg-dialog-overlay')!
    expect(Number(confirmOverlay.style.zIndex)).toBe(1610)
    expect(Number(confirmOverlay.style.zIndex)).toBeGreaterThan(Number(child.style.zIndex))
  })

  it('makes the parent inert while the child is up, and live again after it closes', async () => {
    await mountReview()
    expect(parentButton('.gmr-ft-actions .btn-primary').disabled).toBe(false)
    expect(parentButton('.modal-close').disabled).toBe(false)

    await openChild()
    expect(parentButton('.gmr-ft-actions .btn-primary').disabled).toBe(true)
    expect(rejectTrigger().disabled).toBe(true)
    expect(parentButton('.modal-close').disabled).toBe(true)

    action('cancel').click()
    await flushPromises()
    expect(parentButton('.gmr-ft-actions .btn-primary').disabled).toBe(false)
    expect(parentButton('.modal-close').disabled).toBe(false)
  })

  /**
   * T0020 §4-8. The initial focus is new (nothing in the old hand-rolled box was focused), and
   * the return is the reason `openRejectPrompt` now records the trigger element at all.
   *
   * The trigger is disabled while the child is up — that is §4-6's guard — so focus return had
   * to survive a target that is unfocusable at close time and focusable again one tick later.
   * That is exactly what this asserts, for the confirm path as well as the cancel path.
   */
  it('starts focus in the reason field and returns it to [반려] on cancel', async () => {
    await mountReview()
    const trigger = await openChild()
    expect(document.activeElement).toBe(reasonBox())

    action('cancel').click()
    await flushPromises()

    expect(document.body.querySelector('[data-dialog-variant="form-actions"]')).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it('returns focus to [반려] after the reject is confirmed', async () => {
    await mountReview()
    const trigger = await openChild()
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'rejected' } } })

    reasonBox().value = 'yaml 쪽을 다시 해 주세요'
    reasonBox().dispatchEvent(new Event('input'))
    await flushPromises()
    action('reject-confirm').click()
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      expect.stringContaining('/reject'),
      expect.objectContaining({ reason: 'yaml 쪽을 다시 해 주세요', provider_id: 'p1' }),
    )
    expect(document.body.querySelector('[data-dialog-variant="form-actions"]')).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })
})

/* — the two pure extractions (T0020 §4-9 · §4-10) — */

describe('GitStatusPanelDialog, extracted from GitActionMenu', () => {
  function mountPanel(props: Record<string, unknown> = {}) {
    return track(mount(GitStatusPanelDialog, mountOptions({
      props: { open: true, projectId: 'flowgate', ...props },
      global: { plugins: [i18n], stubs: { GitStatusPanel: true } },
    })))
  }

  it('is a readonly surface with no footer, closed only by the X', async () => {
    const wrapper = mountPanel()
    expect(surface()?.getAttribute('data-dialog-variant')).toBe('readonly')
    // §2.2-2 of the 4순위 step still holds: no footer was invented for it.
    expect(document.body.querySelector('.fg-dialog-footer')).toBeNull()

    clickBackdrop()
    await flushPromises()
    expect(wrapper.emitted('close')).toBeFalsy()

    headerClose().click()
    await flushPromises()
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('relays the panel it hosts without owning its data', () => {
    mountPanel()
    expect(document.body.querySelector('git-status-panel-stub')).not.toBeNull()
    // No project id, nothing to show — the guard the host component carried moved with it.
    wrappers.pop()?.unmount()
    resetDialogSystem()
    document.body.innerHTML = ''
    mountPanel({ projectId: null })
    expect(document.body.querySelector('git-status-panel-stub')).toBeNull()
  })
})

describe('NotificationAiDetailDialog, extracted from NotificationCenter', () => {
  const DETAIL = {
    run_id: 'run1',
    succeeded: true,
    doc_ref: 'flowgate.default.0560.0020-T',
    doc_title: '4.5순위',
    stop_code: 'completed',
    end_reason: null,
    finished_at: null,
    provider_name: 'Claude Sonnet 5',
    last_message: 'done',
    stop_reason: null,
  }

  function mountDetail(props: Record<string, unknown> = {}) {
    return track(mount(NotificationAiDetailDialog as never, mountOptions({
      props: { open: true, detail: DETAIL, loading: false, errored: false, returnFocusTo: null, ...props },
    })))
  }

  it('keeps [문서 열기] [닫기] as aux/dismiss with no primary', () => {
    mountDetail()
    expect(surface()?.getAttribute('data-dialog-variant')).toBe('readonly')
    expect(roles()).toEqual(['aux', 'dismiss'])
    expect(actionIds()).toEqual(['open-document', 'close'])
    expect(document.body.querySelector('[data-dialog-action-role="primary"]')).toBeNull()
  })

  it('drops [문서 열기] when the run points at no document', () => {
    mountDetail({ detail: { ...DETAIL, doc_ref: null } })
    expect(actionIds()).toEqual(['close'])
  })

  it('shows the loading and failure messages the host used to compute', async () => {
    const wrapper = mountDetail({ detail: null, loading: true })
    const message = () => document.body.querySelector('.notif-detail-message')?.textContent
    expect(message()).toBe(i18n.global.t('main.notif_center.ai_detail_loading'))

    await wrapper.setProps({ loading: false, errored: true })
    expect(message()).toBe(i18n.global.t('main.notif_center.ai_detail_failed'))

    await wrapper.setProps({ errored: false, detail: { ...DETAIL, last_message: null, stop_reason: null } })
    expect(message()).toBe(i18n.global.t('main.notif_center.ai_no_message'))
  })

  it('stays put on a backdrop click, as it did before the extraction', async () => {
    const wrapper = mountDetail()
    clickBackdrop()
    await flushPromises()
    expect(wrapper.emitted('close')).toBeFalsy()
    expect(surface()).not.toBeNull()
  })
})
