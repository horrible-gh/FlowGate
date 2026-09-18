import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { h, nextTick } from 'vue'
import i18n from '@shared/i18n'

// flowgate.default.0560 T0012 §2.3 / §5-3 — DialogShell contract.
//
// L0009 §5 "완료 조건" items proved here:
//   1  only the active dialog (= stack.top()) handles ESC / backdrop / focus
//   2  closing a nested child does not close its parent
//   3  focus trap and focus return
//   4  blocking forbids every arbitrary close
//   5  busy and blocking are separate
//  18  teardown has two entry points but one procedure
//  19  the terminating effects happen exactly once, whatever the call order
//  23  the body scroll-lock refcount matches per-entry occupancy and never goes negative
//  24  focus return skips elements inside a dialog that is being torn down
//  25  opt-out boolean props carry explicit defaults instead of Vue's implicit `false`
//
// Everything is driven through the real components and the real (singleton) stack; the
// only thing stubbed out is the app around them.
import DialogFooter from '@main/components/dialogs/DialogFooter.vue'
import DialogHeader from '@main/components/dialogs/DialogHeader.vue'
import DialogShell from '@main/components/dialogs/DialogShell.vue'
import { dialogZOrder, type DialogAction } from '@main/components/dialogs/dialogTypes'
import { dialogStackEntries, resetDialogSystem, scrollLockCount } from '@main/composables/useDialogStack'

const wrappers: VueWrapper[] = []

function mountShell(
  props: Record<string, unknown> = {},
  slots: Record<string, unknown> = {},
): VueWrapper {
  const wrapper = mount(DialogShell as never, {
    // `ariaLabel` by default so the bare shells these tests mount do not each trip the
    // "no title element and no ariaLabel" contract warning; the ARIA block below drives
    // that branch on purpose.
    props: { open: true, variant: 'confirm', ariaLabel: 'test dialog', ...props },
    slots,
    global: { plugins: [i18n] },
    attachTo: document.body,
  })
  wrappers.push(wrapper)
  return wrapper
}

function overlays(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('.fg-dialog-overlay'))
}

function surfaces(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('.fg-dialog-surface'))
}

function pressEscape(): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
}

function clickBackdrop(overlay: HTMLElement, downOn: HTMLElement = overlay): void {
  downOn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
  overlay.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
}

function action(id: string, role: DialogAction['role'], onSelect: () => void = () => {}): DialogAction {
  return { id, label: id, role, onSelect }
}

beforeEach(() => {
  document.body.innerHTML = '<main id="app-main"></main>'
  document.body.style.overflow = ''
})

afterEach(() => {
  // Unmount before resetting: the components own live entries, and tearing the
  // singleton down underneath them would make their own cleanup look like an underflow.
  while (wrappers.length > 0) {
    const wrapper = wrappers.pop()
    try {
      wrapper?.unmount()
    } catch {
      // already unmounted by the test
    }
  }
  resetDialogSystem()
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

describe('DialogShell — resolved defaults (L0009 §1 boolean props 기본값, 완료조건 25)', () => {
  it('gives an unconfigured confirm dialog closeable/closeOnEscape true and closeOnBackdrop false', async () => {
    mountShell()
    await flushPromises()

    const entry = dialogStackEntries()[0]
    expect(entry.policy.closeable).toBe(true)
    expect(entry.policy.closeOnEscape).toBe(true)
    expect(entry.policy.closeOnBackdrop).toBe(false)
    expect(entry.policy.blocking).toBe(false)
    expect(entry.policy.busy).toBe(false)
  })

  it('resolves closeOnBackdrop=false from the variant table for compact with no override (0560 T0035)', async () => {
    mountShell({ variant: 'compact' })
    await flushPromises()
    expect(dialogStackEntries()[0].policy.closeOnBackdrop).toBe(false)
  })

  it('resolves closeOnBackdrop=false from the variant table for readonly with no override (0560 T0035)', async () => {
    mountShell({ variant: 'readonly' })
    await flushPromises()
    expect(dialogStackEntries()[0].policy.closeOnBackdrop).toBe(false)
  })

  it('lets an explicit prop beat the variant default in both directions', async () => {
    mountShell({ variant: 'compact', closeOnBackdrop: true, closeOnEscape: false })
    await flushPromises()
    const entry = dialogStackEntries()[0]
    // closeOnBackdrop: compact's own default is false (0560 T0035) — the explicit `true` wins.
    expect(entry.policy.closeOnBackdrop).toBe(true)
    // closeOnEscape: compact's default is true — the explicit `false` wins.
    expect(entry.policy.closeOnEscape).toBe(false)
  })
})

describe('DialogShell — ESC and backdrop (L0009 §4, 완료조건 1/4/5)', () => {
  it('emits request-close("escape") for a closable dialog', async () => {
    const wrapper = mountShell()
    await flushPromises()

    pressEscape()
    expect(wrapper.emitted('request-close')).toEqual([['escape']])
    // The shell reports the request; it never flips `open` itself.
    expect(overlays()).toHaveLength(1)
  })

  it('ignores ESC and backdrop for a blocking dialog', async () => {
    const wrapper = mountShell({ variant: 'blocking', closeOnBackdrop: true })
    await flushPromises()

    pressEscape()
    clickBackdrop(overlays()[0])

    expect(wrapper.emitted('request-close')).toBeUndefined()
  })

  it('keeps ESC working while busy — busy is not blocking', async () => {
    const wrapper = mountShell({ variant: 'form', busy: true })
    await flushPromises()

    pressEscape()
    expect(wrapper.emitted('request-close')).toEqual([['escape']])
  })

  it('ignores ESC for a progress dialog, whose variant default is closeOnEscape=false', async () => {
    const wrapper = mountShell({ variant: 'progress' })
    await flushPromises()

    pressEscape()
    expect(wrapper.emitted('request-close')).toBeUndefined()
  })

  it('closes on a backdrop press+release that both land on the overlay', async () => {
    // compact's own default is false since 0560 T0035; opt in explicitly to drive this path.
    const wrapper = mountShell({ variant: 'compact', closeOnBackdrop: true })
    await flushPromises()

    clickBackdrop(overlays()[0])
    expect(wrapper.emitted('request-close')).toEqual([['backdrop']])
  })

  it('ignores a drag that starts inside the dialog and ends on the overlay', async () => {
    const wrapper = mountShell({ variant: 'compact', closeOnBackdrop: true })
    await flushPromises()

    clickBackdrop(overlays()[0], surfaces()[0])
    expect(wrapper.emitted('request-close')).toBeUndefined()
  })

  it('ignores a backdrop click when closeOnBackdrop is false', async () => {
    const wrapper = mountShell({ variant: 'form' })
    await flushPromises()

    clickBackdrop(overlays()[0])
    expect(wrapper.emitted('request-close')).toBeUndefined()
  })
})

describe('DialogShell — nested dialogs (L0009 §2 Dialog stack, 완료조건 1/2)', () => {
  it('routes ESC to the child only and leaves the parent open when the child closes', async () => {
    const parent = mountShell({ variant: 'form' })
    await flushPromises()
    const child = mountShell({ variant: 'confirm' })
    await flushPromises()

    expect(dialogStackEntries()).toHaveLength(2)

    pressEscape()
    expect(child.emitted('request-close')).toEqual([['escape']])
    expect(parent.emitted('request-close')).toBeUndefined()

    // The parent is not even a candidate for a backdrop click while a child is up.
    clickBackdrop(overlays()[0])
    expect(parent.emitted('request-close')).toBeUndefined()

    await child.setProps({ open: false })
    await flushPromises()

    expect(dialogStackEntries()).toHaveLength(1)
    expect(parent.emitted('closed')).toBeUndefined()
    expect(child.emitted('closed')).toHaveLength(1)

    // With the child gone the parent is active again.
    pressEscape()
    expect(parent.emitted('request-close')).toEqual([['escape']])
  })

  it('stacks z-order monotonically and keeps the surface above its own overlay', async () => {
    mountShell({ variant: 'form' })
    await flushPromises()
    mountShell({ variant: 'confirm' })
    await flushPromises()

    const [parentOverlay, childOverlay] = overlays()
    const [parentSurface, childSurface] = surfaces()

    expect(parentOverlay.style.zIndex).toBe(String(dialogZOrder.base))
    expect(childOverlay.style.zIndex).toBe(String(dialogZOrder.base + dialogZOrder.step))
    expect(Number(parentSurface.style.zIndex)).toBe(Number(parentOverlay.style.zIndex) + dialogZOrder.surfaceOffset)
    expect(Number(childSurface.style.zIndex)).toBe(Number(childOverlay.style.zIndex) + dialogZOrder.surfaceOffset)
    // 0560 T0012 §4-1: the whole layer sits above every legacy dialog (max 1500) and
    // below the 2000 toast layer.
    expect(dialogZOrder.base).toBeGreaterThan(1500)
    expect(Number(childSurface.style.zIndex)).toBeLessThan(2000)
  })

  it('marks the demoted parent inert while a child is active', async () => {
    mountShell({ variant: 'form' })
    await flushPromises()
    mountShell({ variant: 'confirm' })
    await flushPromises()

    const [parentSurface, childSurface] = surfaces()
    expect(parentSurface.hasAttribute('inert')).toBe(true)
    expect(childSurface.hasAttribute('inert')).toBe(false)
  })
})

describe('DialogShell — focus (L0009 §2/§4, 완료조건 3/24)', () => {
  it('focuses the primary button by default and cancel first on a danger confirm', async () => {
    const actions = [action('cancel', 'cancel'), action('confirm', 'primary')]

    mountShell({ variant: 'confirm' }, { footer: () => h(DialogFooter, { actions }) })
    await flushPromises()
    expect((document.activeElement as HTMLElement).dataset.dialogActionRole).toBe('primary')

    wrappers.pop()?.unmount()

    mountShell({ variant: 'confirm-danger' }, { footer: () => h(DialogFooter, { actions }) })
    await flushPromises()
    expect((document.activeElement as HTMLElement).dataset.dialogActionRole).toBe('cancel')
  })

  it('prefers an explicit autofocus target over the primary button', async () => {
    mountShell(
      { variant: 'form' },
      {
        default: () => h('input', { 'data-dialog-autofocus': '', 'data-testid': 'first-field' }),
        footer: () => h(DialogFooter, { actions: [action('confirm', 'primary')] }),
      },
    )
    await flushPromises()

    expect((document.activeElement as HTMLElement).dataset.testid).toBe('first-field')
  })

  it('wraps Tab at the end and Shift+Tab at the start of the dialog', async () => {
    mountShell(
      { variant: 'form' },
      {
        default: () => [
          h('button', { 'data-testid': 'first' }, 'first'),
          h('button', { 'data-testid': 'last' }, 'last'),
        ],
      },
    )
    await flushPromises()

    const surface = surfaces()[0]
    const first = document.querySelector<HTMLElement>('[data-testid="first"]')!
    const last = document.querySelector<HTMLElement>('[data-testid="last"]')!

    last.focus()
    surface.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    expect(document.activeElement).toBe(first)

    first.focus()
    surface.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }))
    expect(document.activeElement).toBe(last)
  })

  it('pulls focus back when it escapes the active dialog', async () => {
    const outside = document.createElement('button')
    outside.dataset.testid = 'outside'
    document.body.appendChild(outside)

    mountShell({ variant: 'form' }, { default: () => h('button', { 'data-testid': 'inside' }, 'inside') })
    await flushPromises()

    outside.focus()
    outside.dispatchEvent(new FocusEvent('focusin', { bubbles: true }))

    expect((document.activeElement as HTMLElement).dataset.testid).toBe('inside')
  })

  it('returns focus to the trigger that opened the dialog', async () => {
    const trigger = document.createElement('button')
    trigger.dataset.testid = 'trigger'
    document.body.appendChild(trigger)
    trigger.focus()

    const wrapper = mountShell({ variant: 'form' }, { default: () => h('button', {}, 'inside') })
    await flushPromises()
    expect(document.activeElement).not.toBe(trigger)

    await wrapper.setProps({ open: false })
    await flushPromises()

    expect(document.activeElement).toBe(trigger)
  })

  it('never returns focus into a dialog that is being torn down (cascade)', async () => {
    const outside = document.createElement('button')
    outside.dataset.testid = 'outside'
    document.body.appendChild(outside)
    outside.focus()

    const parent = mountShell(
      { variant: 'form' },
      { default: () => h('button', { 'data-testid': 'child-trigger' }, 'open child') },
    )
    await flushPromises()

    const childTrigger = document.querySelector<HTMLElement>('[data-testid="child-trigger"]')!
    childTrigger.focus()

    mountShell({ variant: 'confirm' }, { default: () => h('button', {}, 'inside child') })
    await flushPromises()

    // The parent disappears while the child is still open: the child's own trigger lives
    // inside the parent that is disposing, so it must not be focused.
    parent.unmount()
    await flushPromises()

    expect(document.activeElement).toBe(outside)
    expect(dialogStackEntries()).toHaveLength(0)
  })
})

describe('DialogShell — ARIA (L0009 §2 ARIA)', () => {
  it('labels the dialog with the header title element it actually rendered', async () => {
    mountShell({ variant: 'form' }, { header: () => h(DialogHeader, { title: 'Sequence edit' }) })
    await flushPromises()

    const surface = surfaces()[0]
    const labelledBy = surface.getAttribute('aria-labelledby')
    expect(labelledBy).toBeTruthy()
    const title = document.getElementById(labelledBy as string)
    expect(title?.textContent).toContain('Sequence edit')
    expect(surface.getAttribute('aria-label')).toBeNull()
    expect(surface.getAttribute('role')).toBe('dialog')
    expect(surface.getAttribute('aria-modal')).toBe('true')
  })

  it('warns in development when a dialog has neither a title element nor an aria-label', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    mountShell({ variant: 'compact', ariaLabel: undefined })
    await flushPromises()

    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('neither a title element nor ariaLabel'))
    // Production fallback: render without aria-labelledby rather than point at nothing.
    const surface = surfaces()[0]
    expect(surface.getAttribute('aria-labelledby')).toBeNull()
    expect(surface.getAttribute('aria-label')).toBeNull()
    warnSpy.mockRestore()
  })

  it('falls back to aria-label for a headerless shell and never points at a missing id', async () => {
    mountShell({ variant: 'compact', ariaLabel: 'Quick open' })
    await flushPromises()

    const surface = surfaces()[0]
    expect(surface.getAttribute('aria-label')).toBe('Quick open')
    expect(surface.getAttribute('aria-labelledby')).toBeNull()
  })
})

describe('DialogShell — teardown and scroll lock (L0009 §2/§3, 완료조건 18/19/23)', () => {
  it('locks the body once per open entry and unlocks only when the last one goes', async () => {
    expect(scrollLockCount()).toBe(0)

    const parent = mountShell({ variant: 'form' })
    await flushPromises()
    expect(scrollLockCount()).toBe(1)
    expect(document.body.style.overflow).toBe('hidden')

    const child = mountShell({ variant: 'confirm' })
    await flushPromises()
    expect(scrollLockCount()).toBe(2)

    await child.setProps({ open: false })
    await flushPromises()
    expect(scrollLockCount()).toBe(1)
    // The parent is still open, so the page must stay locked.
    expect(document.body.style.overflow).toBe('hidden')

    await parent.setProps({ open: false })
    await flushPromises()
    expect(scrollLockCount()).toBe(0)
    expect(document.body.style.overflow).toBe('')
  })

  it('runs every terminating effect exactly once across both teardown entry points', async () => {
    // A listener prop, not wrapper.emitted(): the second half of this test needs the
    // count to survive the unmount that provokes the forced teardown.
    const onClosed = vi.fn()
    const wrapper = mountShell({ variant: 'form', onClosed })
    await flushPromises()
    expect(scrollLockCount()).toBe(1)

    // Normal close first…
    await wrapper.setProps({ open: false })
    await flushPromises()
    expect(onClosed).toHaveBeenCalledTimes(1)
    expect(scrollLockCount()).toBe(0)
    expect(dialogStackEntries()).toHaveLength(0)

    // …then the defensive forced path on the very same dialog.
    wrapper.unmount()
    await flushPromises()

    expect(onClosed).toHaveBeenCalledTimes(1)
    expect(scrollLockCount()).toBe(0)
    expect(document.body.style.overflow).toBe('')
  })

  it('cannot drive the refcount below zero when a dialog dies before it finished opening', async () => {
    const wrapper = mountShell({ variant: 'form' })
    // No flush: the surface has not been wired up yet.
    await nextTick()
    wrapper.unmount()
    await flushPromises()

    expect(scrollLockCount()).toBe(0)
    expect(document.body.style.overflow).toBe('')
  })
})
