import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'

// flowgate.default.0560 T0012 §2.6 / §2.7 — declarative ConfirmDialog / AlertDialog.
//
// The wrapper contract of L0009 §2: `open` is passed through and never flipped by the
// component, and every close request that is not the confirm button — header X, ESC,
// backdrop — comes out as the same `cancel` (for alerts: the same `close`). That single
// rule is what stops "X cancels but ESC saves" from reappearing screen by screen.
//
// The default labels are the T0012 §4-3 decision: reuse the existing i18n keys
// (`common.confirm` / `common.cancel` / `common.close`) that `WorkflowDecisionModal.vue`
// and `ConfirmModal.vue` already use, rather than inventing new copy.
import AlertDialog from '@main/components/dialogs/AlertDialog.vue'
import ConfirmDialog from '@main/components/dialogs/ConfirmDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'

const wrappers: VueWrapper[] = []

function mountConfirm(props: Record<string, unknown> = {}): VueWrapper {
  const wrapper = mount(ConfirmDialog as never, {
    props: { open: true, title: 'Discard changes?', ...props },
    global: { plugins: [i18n] },
    attachTo: document.body,
  })
  wrappers.push(wrapper)
  return wrapper
}

function mountAlert(props: Record<string, unknown> = {}): VueWrapper {
  const wrapper = mount(AlertDialog as never, {
    props: { open: true, title: 'Clipboard blocked', ...props },
    global: { plugins: [i18n] },
    attachTo: document.body,
  })
  wrappers.push(wrapper)
  return wrapper
}

function clickRole(role: 'primary' | 'cancel'): void {
  document
    .querySelector<HTMLElement>(`[data-dialog-action-role="${role}"]`)
    ?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

function clickHeaderClose(): void {
  document
    .querySelector<HTMLElement>('.fg-dialog-header__close')
    ?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

function pressEscape(): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
}

function labelOf(role: 'primary' | 'cancel'): string {
  return document.querySelector<HTMLElement>(`[data-dialog-action-role="${role}"]`)?.textContent?.trim() ?? ''
}

beforeEach(() => {
  document.body.innerHTML = '<main id="app-main"></main>'
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

describe('ConfirmDialog — declarative (L0009 §2 ConfirmDialog)', () => {
  it('emits confirm for the confirm button and cancel for the cancel button', async () => {
    const wrapper = mountConfirm()
    await flushPromises()

    clickRole('primary')
    await flushPromises()
    expect(wrapper.emitted('confirm')).toHaveLength(1)
    expect(wrapper.emitted('cancel')).toBeUndefined()

    clickRole('cancel')
    await flushPromises()
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('turns the header X, ESC and a backdrop click into the same cancel', async () => {
    const wrapper = mountConfirm({ closeOnBackdrop: true })
    await flushPromises()

    clickHeaderClose()
    pressEscape()
    const overlay = document.querySelector<HTMLElement>('.fg-dialog-overlay')!
    overlay.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    overlay.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await flushPromises()

    expect(wrapper.emitted('cancel')).toHaveLength(3)
    expect(wrapper.emitted('confirm')).toBeUndefined()
  })

  it('never flips `open` itself — the feature owns it', async () => {
    const wrapper = mountConfirm()
    await flushPromises()

    clickRole('cancel')
    await flushPromises()

    // Still on screen: the dialog reported the intent and waits for the feature.
    expect(document.querySelectorAll('.fg-dialog-surface')).toHaveLength(1)
    expect(wrapper.props('open')).toBe(true)

    await wrapper.setProps({ open: false })
    await flushPromises()
    expect(document.querySelectorAll('.fg-dialog-surface')).toHaveLength(0)
    expect(wrapper.emitted('closed')).toHaveLength(1)
  })

  it('uses the existing common.confirm / common.cancel labels by default', async () => {
    mountConfirm()
    await flushPromises()

    expect(labelOf('primary')).toBe(i18n.global.t('common.confirm'))
    expect(labelOf('cancel')).toBe(i18n.global.t('common.cancel'))
  })

  it('lets a caller override either label', async () => {
    mountConfirm({ confirmLabel: 'Unmerge', cancelLabel: 'Keep' })
    await flushPromises()

    expect(labelOf('primary')).toBe('Unmerge')
    expect(labelOf('cancel')).toBe('Keep')
  })

  it('renders danger=true as the confirm-danger variant', async () => {
    mountConfirm({ danger: true })
    await flushPromises()

    const surface = document.querySelector<HTMLElement>('.fg-dialog-surface')
    expect(surface?.dataset.dialogVariant).toBe('confirm-danger')
  })

  it('describes the dialog with its own message element', async () => {
    mountConfirm({ message: 'Unsaved edits will be lost.' })
    await flushPromises()

    const surface = document.querySelector<HTMLElement>('.fg-dialog-surface')!
    const describedBy = surface.getAttribute('aria-describedby')
    expect(describedBy).toBeTruthy()
    expect(document.getElementById(describedBy as string)?.textContent).toContain('Unsaved edits will be lost.')
  })
})

describe('AlertDialog — declarative (L0009 §2 AlertDialog)', () => {
  it('emits close for the button, the header X and ESC alike', async () => {
    const wrapper = mountAlert()
    await flushPromises()

    clickRole('primary')
    clickHeaderClose()
    pressEscape()
    await flushPromises()

    expect(wrapper.emitted('close')).toHaveLength(3)
  })

  it('uses the existing common.close label by default and keeps a danger tone visible', async () => {
    mountAlert({ tone: 'danger' })
    await flushPromises()

    expect(labelOf('primary')).toBe(i18n.global.t('common.close'))
    const primary = document.querySelector<HTMLElement>('[data-dialog-action-role="primary"]')
    expect(primary?.className).toContain('fg-dialog-btn--tone-danger')
  })
})
