/**
 * flowgate.default.0560 T0016 (3순위) — the five representative dialogs on the common layer.
 *
 * R0001 was "취소 버튼이 제각각". NR0005 §13 named these five as the direct evidence: five
 * cancel/close buttons with three different base classes and two different positions. This
 * spec pins the outcome of both stages of T0016 at once, because only the pair is a fix:
 *
 *   1단계 — the class/position normalisation (§2.1). `GitBaseDirtyDialog`'s cancel sat LEFT
 *           of a destructive action, `WorkflowDecisionModal`'s edit-mode cancel repainted
 *           itself with the danger hue, `ReviewRejectDialog`'s close was a `btn-outline`
 *           pinned to the far right by `margin-left: auto`.
 *   2단계 — the migration onto `DialogShell`/`DialogHeader`/`DialogFooter` (§2.2), after
 *           which the order is no longer a property of any of these files: `footerRolePriority`
 *           sorts `aux → danger → stop → cancel → primary` before rendering.
 *
 * The assertions below are therefore DOM-order and rendered-class assertions, not source
 * greps — a file could be re-edited into the old layout and still "contain DialogFooter".
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'

const postRequest = vi.fn()
const getRequest = vi.fn()
const patchRequest = vi.fn()

// Only the three request helpers are replaced; everything else in the module stays real so
// the stores these dialogs pull in keep working.
vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    postRequest: (...args: unknown[]) => postRequest(...args),
    getRequest: (...args: unknown[]) => getRequest(...args),
    patchRequest: (...args: unknown[]) => patchRequest(...args),
  }
})

vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

vi.mock('@main/utils/clipboard', () => ({
  ClipboardAbort: class ClipboardAbort extends Error {},
  copyToClipboardDeferred: async () => true,
  consumeLastFailedCopyText: () => null,
}))

vi.mock('@main/composables/useFlowGateToken', async () => {
  // `issuing` has to be a real ref — the edit-mode aux actions read it for their disabled
  // state, and a plain object unwraps truthy and would disable them for the wrong reason.
  const { ref } = await import('vue')
  const issuing = ref(false)
  return {
    useFlowGateToken: () => ({
      requestSequenceEdit: vi.fn(),
      composeMention: () => 'MENTION',
      issuing,
    }),
  }
})

import ConfirmModal from '@main/components/ConfirmModal.vue'
import GitBaseDirtyDialog from '@main/components/GitBaseDirtyDialog.vue'
import GroupDiscardModal from '@main/components/GroupDiscardModal.vue'
import ReviewRejectDialog from '@main/components/ReviewRejectDialog.vue'
import WorkflowDecisionModal from '@main/components/WorkflowDecisionModal.vue'
import DialogFooter from '@main/components/dialogs/DialogFooter.vue'
import type { DialogAction } from '@main/components/dialogs/dialogTypes'
import { resetDialogSystem } from '@main/composables/useDialogStack'

const COMPONENTS_DIR = resolve(__dirname, '../../src/main/components')

const wrappers: VueWrapper[] = []

function track<T extends VueWrapper>(wrapper: T): T {
  wrappers.push(wrapper)
  return wrapper
}

/** Footer roles in the order the browser paints them, left to right. */
function renderedRoles(): string[] {
  return Array.from(document.querySelectorAll('[data-dialog-action-role]')).map(
    (el) => el.getAttribute('data-dialog-action-role') ?? '',
  )
}

function actionButton(id: string): HTMLButtonElement | null {
  return document.querySelector<HTMLButtonElement>(`[data-dialog-action-id="${id}"]`)
}

function cancelButton(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-dialog-action-role="cancel"]')
}

function mountOptions(extra: Record<string, unknown> = {}) {
  return { global: { plugins: [i18n] }, attachTo: document.body, ...extra }
}

beforeEach(() => {
  setActivePinia(createPinia())
  postRequest.mockReset().mockResolvedValue({ data: { ok: true, result: { remaining: [] } } })
  patchRequest.mockReset().mockResolvedValue({ data: { status: 'updated' } })
  getRequest.mockReset().mockImplementation((url: string) => {
    if (String(url).includes('/ai-invoke/providers')) {
      return Promise.resolve({
        data: {
          ok: true,
          project: 'flowgate',
          providers: [{ id: 'claude', name: 'Claude', exec_type: 'cli', kind: 'claude' }],
          default_provider_id: 'claude',
        },
      })
    }
    return Promise.resolve({
      data: {
        items: [
          { type: 'N', label: 'investigate', status: 'done', note: '', note_source: null, provider_id: null },
          { type: 'T', label: 'implement', status: 'pending', note: '', note_source: null, provider_id: null },
        ],
      },
    })
  })
  i18n.global.locale.value = 'ko'
  document.body.innerHTML = ''
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

/* ───────────────────── 1. every instance is really on the shell ───────────────────── */

const MIGRATED_FILES = [
  'ConfirmModal.vue',
  'GroupDiscardModal.vue',
  'GitBaseDirtyDialog.vue',
  'WorkflowDecisionModal.vue',
  'ReviewRejectDialog.vue',
]

describe('3순위 migration — the old modal markup is gone (T0016 §4-2)', () => {
  it.each(MIGRATED_FILES)('%s renders no legacy .modal-* element', (file) => {
    const source = readFileSync(resolve(COMPONENTS_DIR, file), 'utf8')
    // Matched on class ATTRIBUTES, not on the word: a prose comment may still refer to the
    // markup this migration replaced without re-introducing it.
    const legacyClassUse = source.match(/class="[^"]*\bmodal-(bg|box|hd|bd|ft|close|title)\b/g)
    expect(legacyClassUse, `legacy modal markup still in ${file}`).toBeNull()
    expect(source).not.toContain('<teleport to="body">')
  })

  it.each(MIGRATED_FILES)('%s builds on DialogShell + DialogHeader + DialogFooter', (file) => {
    const source = readFileSync(resolve(COMPONENTS_DIR, file), 'utf8')
    for (const tag of ['<DialogShell', '<DialogHeader', '<DialogFooter']) {
      expect(source, `${file} is missing ${tag}`).toContain(tag)
    }
  })
})

/* ─────────────────────── 2. rendered footer order per instance ─────────────────────── */

describe('rendered footer order matches footerRolePriority (T0016 §4-3)', () => {
  it('ConfirmModal — [취소] [확인]', async () => {
    track(mount(ConfirmModal, mountOptions({
      props: { visible: true, title: '삭제할까요', message: '되돌릴 수 없습니다' },
    })))
    await flushPromises()
    expect(renderedRoles()).toEqual(['cancel', 'primary'])
  })

  it('GroupDiscardModal — [취소] [폐기(위험 주버튼)]', async () => {
    track(mount(GroupDiscardModal, mountOptions({
      props: { visible: true, groupTitle: '0560', documents: [] },
    })))
    await flushPromises()
    expect(renderedRoles()).toEqual(['cancel', 'primary'])
    // D0008 §6 Danger Confirm: the destructive action keeps the primary POSITION and only
    // carries the danger tone.
    expect(actionButton('confirm')?.className).toContain('fg-dialog-btn--tone-danger')
  })

  it('GitBaseDirtyDialog — [되돌리기(위험)] [취소] [커밋] (the 1단계 inversion, now layer-owned)', async () => {
    const wrapper = track(mount(GitBaseDirtyDialog, mountOptions({ props: { context: 'finalize' } })))
    void (wrapper.vm as unknown as { resolve: (p: string, f: string[]) => Promise<string> })
      .resolve('proj-1', ['a.txt'])
    await flushPromises()
    expect(renderedRoles()).toEqual(['danger', 'cancel', 'primary'])
  })

  it('WorkflowDecisionModal (결정 모드) — [취소] [확인]', async () => {
    track(mount(WorkflowDecisionModal, mountOptions({ props: { visible: true, docClass: 'R' } })))
    await flushPromises()
    expect(renderedRoles()).toEqual(['cancel', 'primary'])
  })

  it('WorkflowDecisionModal (편집 모드) — [멘트복사] [AI호출] [취소] [저장]', async () => {
    track(mount(WorkflowDecisionModal, mountOptions({
      props: { visible: true, mode: 'edit', docId: 'flowgate.default.0560.0016-T' },
    })))
    await flushPromises()
    expect(renderedRoles()).toEqual(['aux', 'aux', 'cancel', 'primary'])
    // 0268 B0001 put 멘트복사 ahead of AI호출; both share the aux weight, so `order` — not
    // the array position — is what keeps them in that sequence.
    const ids = Array.from(document.querySelectorAll('[data-dialog-action-id]')).map((el) =>
      el.getAttribute('data-dialog-action-id'),
    )
    expect(ids).toEqual(['mention-copy', 'invoke-ai', 'cancel', 'save'])
  })

  it('ReviewRejectDialog — [반려 ▼(보조)] [닫기] [저장(위험 주버튼)]', async () => {
    track(mount(ReviewRejectDialog, mountOptions({
      props: { visible: true, docId: 'flowgate.default.0560.0016-T', docName: '[T] 3순위', docType: 'T' },
    })))
    await flushPromises()
    expect(renderedRoles()).toEqual(['aux', 'cancel', 'primary'])
    // §2.2 asked the TR to fix this dialog's roles: 저장 completes the dialog, so it holds
    // the primary position with `tone: 'danger'` — the reading that keeps 닫기 immediately
    // left of it instead of stranding 닫기 at the right edge again.
    expect(actionButton('save')?.className).toContain('fg-dialog-btn--tone-danger')
  })

  it('the order is the layer\'s, not the caller\'s — a shuffled actions array renders sorted', async () => {
    const noop = () => {}
    const shuffled: DialogAction[] = [
      { id: 'commit', label: '커밋', role: 'primary', onSelect: noop },
      { id: 'cancel', label: '취소', role: 'cancel', onSelect: noop },
      { id: 'revert', label: '되돌리기', role: 'danger', onSelect: noop },
    ]
    track(mount(DialogFooter, mountOptions({ props: { actions: shuffled } })))
    await flushPromises()
    expect(renderedRoles()).toEqual(['danger', 'cancel', 'primary'])
  })
})

/* ──────────────────────── 3. one cancel appearance, everywhere ─────────────────────── */

describe('the cancel/close button is one class in all five (R0001)', () => {
  const CANCEL_CLASS = 'fg-dialog-btn fg-dialog-btn--cancel fg-dialog-btn--tone-default'

  async function cancelClassOf(mounter: () => Promise<void>): Promise<string> {
    await mounter()
    const button = cancelButton()
    expect(button).toBeTruthy()
    return button!.className.trim()
  }

  it('ConfirmModal', async () => {
    expect(await cancelClassOf(async () => {
      track(mount(ConfirmModal, mountOptions({ props: { visible: true, title: 't', message: 'm' } })))
      await flushPromises()
    })).toBe(CANCEL_CLASS)
  })

  it('GroupDiscardModal', async () => {
    expect(await cancelClassOf(async () => {
      track(mount(GroupDiscardModal, mountOptions({ props: { visible: true, groupTitle: 'g', documents: [] } })))
      await flushPromises()
    })).toBe(CANCEL_CLASS)
  })

  it('GitBaseDirtyDialog', async () => {
    expect(await cancelClassOf(async () => {
      const wrapper = track(mount(GitBaseDirtyDialog, mountOptions({})))
      void (wrapper.vm as unknown as { resolve: (p: string, f: string[]) => Promise<string> })
        .resolve('proj-1', ['a.txt'])
      await flushPromises()
    })).toBe(CANCEL_CLASS)
  })

  it('WorkflowDecisionModal — 결정 모드와 편집 모드가 같은 취소 버튼을 쓴다', async () => {
    const decide = await cancelClassOf(async () => {
      track(mount(WorkflowDecisionModal, mountOptions({ props: { visible: true, docClass: 'R' } })))
      await flushPromises()
    })
    expect(decide).toBe(CANCEL_CLASS)

    // The same file used to paint its OTHER cancel with `.wdm-cancel-btn` — R0001 inside a
    // single component, not just across files (T0016 §3).
    wrappers.pop()?.unmount()
    resetDialogSystem()
    document.body.innerHTML = ''
    const edit = await cancelClassOf(async () => {
      track(mount(WorkflowDecisionModal, mountOptions({
        props: { visible: true, mode: 'edit', docId: 'flowgate.default.0560.0016-T' },
      })))
      await flushPromises()
    })
    expect(edit).toBe(decide)
  })

  it('ReviewRejectDialog — no btn-outline and no margin-left:auto survivor', async () => {
    expect(await cancelClassOf(async () => {
      track(mount(ReviewRejectDialog, mountOptions({
        props: { visible: true, docId: 'd', docName: 'n', docType: 'T' },
      })))
      await flushPromises()
    })).toBe(CANCEL_CLASS)
    // Matched as real code, not as the word: the file's own comments still explain what
    // T0016 §2.1 #6 removed, and that prose must not be what keeps this test green.
    const source = readFileSync(resolve(COMPONENTS_DIR, 'ReviewRejectDialog.vue'), 'utf8')
    expect(source).not.toMatch(/class="[^"]*\bbtn-outline\b/)
    expect(source).not.toMatch(/^\s*margin-left:\s*auto\s*;/m)
  })
})

/* ──────────────── 4. header X and ESC are the same close as 취소 ───────────────── */

describe('one close meaning per dialog (D0008 §3, L0009 §1 불변식 4)', () => {
  function headerClose(): void {
    document
      .querySelector<HTMLElement>('.fg-dialog-header__close')
      ?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  }

  function pressEscape(): void {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  }

  it('ConfirmModal — X and ESC both cancel, exactly like the 취소 button', async () => {
    const wrapper = track(mount(ConfirmModal, mountOptions({
      props: { visible: true, title: 't', message: 'm' },
    })))
    await flushPromises()
    headerClose()
    await flushPromises()
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    expect(wrapper.emitted('confirm')).toBeUndefined()

    pressEscape()
    await flushPromises()
    expect(wrapper.emitted('cancel')).toHaveLength(2)
  })

  it('GroupDiscardModal — X cancels and never confirms the discard', async () => {
    const wrapper = track(mount(GroupDiscardModal, mountOptions({
      props: { visible: true, groupTitle: 'g', documents: [] },
    })))
    await flushPromises()
    headerClose()
    await flushPromises()
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    expect(wrapper.emitted('confirm')).toBeUndefined()
  })

  it('GitBaseDirtyDialog — X resolves the imperative Promise as a cancel', async () => {
    const wrapper = track(mount(GitBaseDirtyDialog, mountOptions({})))
    const settled = (wrapper.vm as unknown as { resolve: (p: string, f: string[]) => Promise<string> })
      .resolve('proj-1', ['a.txt'])
    await flushPromises()
    headerClose()
    await expect(settled).resolves.toBe('cancel')
  })

  it('WorkflowDecisionModal — X closes without emitting a decision', async () => {
    const wrapper = track(mount(WorkflowDecisionModal, mountOptions({
      props: { visible: true, docClass: 'R' },
    })))
    await flushPromises()
    headerClose()
    await flushPromises()
    expect(wrapper.emitted('update:visible')).toEqual([[false]])
    expect(wrapper.emitted('confirmed')).toBeUndefined()
  })

  it('ReviewRejectDialog — X closes without saving the reason', async () => {
    const wrapper = track(mount(ReviewRejectDialog, mountOptions({
      props: { visible: true, docId: 'd', docName: 'n', docType: 'T', existingReason: '기존 사유' },
    })))
    await flushPromises()
    headerClose()
    await flushPromises()
    expect(wrapper.emitted('update:visible')).toEqual([[false]])
    expect(wrapper.emitted('save-reason')).toBeUndefined()
  })

  it('backdrop stays inert — 0412 T0004 removed outside-click close and the variants keep it off', async () => {
    const wrapper = track(mount(GroupDiscardModal, mountOptions({
      props: { visible: true, groupTitle: 'g', documents: [] },
    })))
    await flushPromises()
    const overlay = document.querySelector<HTMLElement>('.fg-dialog-overlay')!
    overlay.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    overlay.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await flushPromises()
    expect(wrapper.emitted('cancel')).toBeUndefined()
  })
})

/* ─────────────────────────── 5. behaviour survives the move ────────────────────────── */

describe('feature behaviour is unchanged by the migration (T0016 §4-4)', () => {
  it('ConfirmModal still emits confirm / cancel and still renders its extra slot', async () => {
    const wrapper = track(mount(ConfirmModal, mountOptions({
      props: { visible: true, title: '확정', message: '진행합니다', danger: true },
      slots: { default: '<p class="extra-block">git finalize choice</p>' },
    })))
    await flushPromises()
    expect(document.querySelector('.extra-block')).toBeTruthy()
    // danger → the primary keeps its position and turns destructive.
    expect(actionButton('confirm')?.className).toContain('fg-dialog-btn--tone-danger')

    actionButton('confirm')?.click()
    await flushPromises()
    expect(wrapper.emitted('confirm')).toHaveLength(1)
    expect(wrapper.emitted('update:visible')).toEqual([[false]])

    actionButton('cancel')?.click()
    await flushPromises()
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('GroupDiscardModal keeps its double gate and its reopen reset', async () => {
    const wrapper = track(mount(GroupDiscardModal, mountOptions({
      props: { visible: true, groupTitle: '0560', documents: [{ id: '1', typeCode: 'T', shortId: 'T0016' }] },
    })))
    await flushPromises()
    const confirm = actionButton('confirm')!
    expect(confirm.disabled).toBe(true)

    const textarea = document.querySelector<HTMLTextAreaElement>('.gd-textarea')!
    textarea.value = '중복 그룹'
    textarea.dispatchEvent(new Event('input'))
    await flushPromises()
    // Reason alone is not enough — the acknowledgement is the second gate.
    expect(actionButton('confirm')!.disabled).toBe(true)

    const ack = document.querySelector<HTMLInputElement>('.gd-confirm-line input')!
    ack.checked = true
    ack.dispatchEvent(new Event('change'))
    await flushPromises()
    expect(actionButton('confirm')!.disabled).toBe(false)

    actionButton('confirm')!.click()
    await flushPromises()
    expect(wrapper.emitted('confirm')).toEqual([['중복 그룹']])

    // Reopening clears both gates so one group's reason never carries into the next.
    await wrapper.setProps({ visible: false })
    await wrapper.setProps({ visible: true })
    await flushPromises()
    expect(document.querySelector<HTMLTextAreaElement>('.gd-textarea')!.value).toBe('')
    expect(actionButton('confirm')!.disabled).toBe(true)
  })

  it('GitBaseDirtyDialog keeps the imperative resolve() Promise contract', async () => {
    const wrapper = track(mount(GitBaseDirtyDialog, mountOptions({ props: { context: 'finalize' } })))
    const vm = wrapper.vm as unknown as { resolve: (p: string, f: string[]) => Promise<'proceed' | 'cancel'> }

    const cancelled = vm.resolve('proj-1', ['a.txt', 'b.txt'])
    await flushPromises()
    expect(document.querySelector('.fg-dialog-overlay')).toBeTruthy()
    actionButton('cancel')!.click()
    await expect(cancelled).resolves.toBe('cancel')
    await flushPromises()
    expect(document.querySelector('.fg-dialog-overlay')).toBeNull()

    // A clean base after the commit resolves 'proceed' — the caller then retries the finalize.
    const proceeded = vm.resolve('proj-1', ['a.txt'])
    await flushPromises()
    actionButton('commit')!.click()
    await expect(proceeded).resolves.toBe('proceed')
  })

  it('GitBaseDirtyDialog still refuses a revert with no files to revert', async () => {
    const wrapper = track(mount(GitBaseDirtyDialog, mountOptions({})))
    const vm = wrapper.vm as unknown as { resolve: (p: string, f: string[]) => Promise<string> }
    // An empty 409 payload makes the component ask the store, which answers with nothing here.
    void vm.resolve('proj-1', [])
    await flushPromises()
    expect(actionButton('revert')!.disabled).toBe(true)
    expect(actionButton('commit')!.disabled).toBe(false)
  })

  it('WorkflowDecisionModal keeps the provider picker at the far left of the footer row', async () => {
    track(mount(WorkflowDecisionModal, mountOptions({
      props: { visible: true, mode: 'edit', docId: 'flowgate.default.0560.0016-T' },
    })))
    await flushPromises()
    const row = document.querySelector('.wdm-ft-row')!
    const picker = row.querySelector('.wdm-provider')
    expect(picker).toBeTruthy()
    // 0268 TR0005 rev1: it leads the row, ahead of every action.
    expect(Array.from(row.children).indexOf(picker as Element)).toBe(0)
    expect(row.querySelector('.fg-dialog-footer')).toBeTruthy()
  })

  it('WorkflowDecisionModal edit mode exposes no footer actions while it is still loading', async () => {
    // The four edit-mode buttons were `v-if="!loading && !loadError"`; as actions the same
    // gate lives in the computed, so a failed load still shows no half-usable footer.
    getRequest.mockImplementation((url: string) => {
      if (String(url).includes('/ai-invoke/providers')) {
        return Promise.resolve({ data: { ok: true, project: 'flowgate', providers: [], default_provider_id: null } })
      }
      return Promise.reject(new Error('boom'))
    })
    track(mount(WorkflowDecisionModal, mountOptions({
      props: { visible: true, mode: 'edit', docId: 'flowgate.default.0560.0016-T' },
    })))
    await flushPromises()
    expect(renderedRoles()).toEqual([])
  })

  it('ReviewRejectDialog keeps the reject drop-up, its edit-mode absence and its exposed API', async () => {
    const wrapper = track(mount(ReviewRejectDialog, mountOptions({
      props: { visible: true, docId: 'flowgate.default.0560.0016-T', docName: '[T] 3순위', docType: 'T' },
    })))
    await flushPromises()

    const trigger = actionButton('reject-menu')!
    expect(trigger.getAttribute('data-dialog-action-role')).toBe('aux')
    expect(document.querySelector('.rrd-dropdown')).toBeNull()
    // `reject-menu` is a real DialogFooter-rendered DialogAction now, not a hand-placed
    // element the caller stops propagation on — this click bubbles to the window-level
    // outside-click listener exactly like any other document click would. The dropdown
    // must still end up open: closing here would be the exact bug T0016's rework fixed
    // (containment-checking the trigger/panel instead of relying on `.stop`).
    trigger.click()
    await flushPromises()
    expect(document.querySelector('.rrd-dropdown')).toBeTruthy()
    const items = document.querySelectorAll('.rrd-dropdown-item')
    expect(items).toHaveLength(3)

    // A genuine outside click still closes it.
    document.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(document.querySelector('.rrd-dropdown')).toBeNull()

    trigger.click()
    await flushPromises()
    ;(document.querySelectorAll('.rrd-dropdown-item')[0] as HTMLButtonElement).click()
    await flushPromises()
    expect(wrapper.emitted('copy-mention')).toBeTruthy()

    // save-reason still fires with the trimmed live reason.
    const textarea = document.querySelector<HTMLTextAreaElement>('.rrd-textarea')!
    textarea.value = '  근거가 없습니다  '
    textarea.dispatchEvent(new Event('input'))
    await flushPromises()
    actionButton('save')!.click()
    await flushPromises()
    expect(wrapper.emitted('save-reason')).toEqual([['근거가 없습니다']])

    const vm = wrapper.vm as unknown as { notifySaved: () => void; notifySaveFailed: () => void }
    expect(typeof vm.notifySaved).toBe('function')
    expect(typeof vm.notifySaveFailed).toBe('function')
    vm.notifySaved()
    expect(wrapper.emitted('update:visible')).toEqual([[false]])
  })

  it('ReviewRejectDialog in editMode drops the reject control and keeps the two actions', async () => {
    track(mount(ReviewRejectDialog, mountOptions({
      props: { visible: true, docId: 'd', docName: 'n', docType: 'T', editMode: true, existingReason: '기존 사유' },
    })))
    await flushPromises()
    expect(actionButton('reject-menu')).toBeNull()
    expect(renderedRoles()).toEqual(['cancel', 'primary'])
  })
})
