import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

// flowgate.default.0560 T0012 §2.6 / §2.7 / §5-3 — imperative confirm()/alert().
//
// L0009 §5 "완료 조건" items proved here:
//   9  an async confirm resolves exactly once
//  10  concurrent confirms are FIFO inside a context
//  12  the native confirm true/false control flow survives the replacement
//  17  parent/nested is decided by capturing stack.top(), with no caller involvement
//  20  a parent teardown flushes its queue's active AND pending requests to false, and
//      a disposing context accepts neither a new enqueue nor a show_next
//
// The host components render the queue; `ConfirmDialog host` / `AlertDialog host` is
// what a later step mounts once at app level so that every `await confirm(...)` call
// site has a surface.
import AlertDialog from '@main/components/dialogs/AlertDialog.vue'
import ConfirmDialog from '@main/components/dialogs/ConfirmDialog.vue'
import DialogShell from '@main/components/dialogs/DialogShell.vue'
import {
  alert as dialogAlert,
  confirm as dialogConfirm,
  dialogStackEntries,
  displayedDialogRequests,
  forceCleanup,
  resetDialogSystem,
} from '@main/composables/useDialogStack'

const wrappers: VueWrapper[] = []

function track(wrapper: VueWrapper): VueWrapper {
  wrappers.push(wrapper)
  return wrapper
}

function mountConfirmHost(): VueWrapper {
  return track(
    mount(ConfirmDialog as never, {
      props: { host: true },
      global: { plugins: [i18n] },
      attachTo: document.body,
    }),
  )
}

function mountAlertHost(): VueWrapper {
  return track(
    mount(AlertDialog as never, {
      props: { host: true },
      global: { plugins: [i18n] },
      attachTo: document.body,
    }),
  )
}

function mountShell(props: Record<string, unknown> = {}): VueWrapper {
  return track(
    mount(DialogShell as never, {
      props: { open: true, variant: 'form', ariaLabel: 'host dialog', ...props },
      global: { plugins: [i18n] },
      attachTo: document.body,
    }),
  )
}

function dialogTitles(): string[] {
  return Array.from(document.querySelectorAll<HTMLElement>('.fg-dialog-header__title')).map(
    (el) => el.textContent?.trim() ?? '',
  )
}

function clickRole(role: 'primary' | 'cancel', index = 0): void {
  const buttons = document.querySelectorAll<HTMLElement>(`[data-dialog-action-role="${role}"]`)
  buttons[index].dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

function pressEscape(): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
}

beforeEach(() => {
  document.body.innerHTML = '<main id="app-main"></main>'
})

afterEach(() => {
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

describe('imperative confirm() — resolution (완료조건 9/12)', () => {
  it('resolves true on confirm, exactly once', async () => {
    mountConfirmHost()
    const resolved = vi.fn()
    void dialogConfirm({ title: 'Delete branch?' }).then(resolved)
    await flushPromises()

    expect(dialogTitles()).toEqual(['Delete branch?'])

    clickRole('primary')
    await flushPromises()

    expect(resolved).toHaveBeenCalledTimes(1)
    expect(resolved).toHaveBeenCalledWith(true)
    expect(displayedDialogRequests()).toHaveLength(0)
    expect(dialogStackEntries()).toHaveLength(0)
  })

  it('resolves false on cancel and on ESC', async () => {
    mountConfirmHost()

    const first = dialogConfirm({ title: 'first' })
    await flushPromises()
    clickRole('cancel')
    await expect(first).resolves.toBe(false)

    const second = dialogConfirm({ title: 'second' })
    await flushPromises()
    pressEscape()
    await expect(second).resolves.toBe(false)
  })

  it('preserves the `if (!confirmed) return` control flow of window.confirm', async () => {
    mountConfirmHost()
    const performed: string[] = []

    async function removeUntracked(): Promise<void> {
      const confirmed = await dialogConfirm({ title: 'Remove untracked files?', danger: true })
      if (!confirmed) return
      performed.push('removed')
    }

    const cancelled = removeUntracked()
    await flushPromises()
    clickRole('cancel')
    await cancelled
    expect(performed).toEqual([])

    const accepted = removeUntracked()
    await flushPromises()
    clickRole('primary')
    await accepted
    expect(performed).toEqual(['removed'])
  })

  it('marks a danger confirm with the danger variant and tone', async () => {
    mountConfirmHost()
    void dialogConfirm({ title: 'Unmerge?', danger: true })
    await flushPromises()

    const surface = document.querySelector<HTMLElement>('.fg-dialog-surface')
    expect(surface?.dataset.dialogVariant).toBe('confirm-danger')
    const primary = document.querySelector<HTMLElement>('[data-dialog-action-role="primary"]')
    expect(primary?.className).toContain('fg-dialog-btn--tone-danger')
    // Danger confirms open with cancel focused, not the destructive button.
    expect((document.activeElement as HTMLElement).dataset.dialogActionRole).toBe('cancel')
  })
})

describe('imperative confirm() — queueing (완료조건 10/17)', () => {
  it('serialises two concurrent root confirms FIFO', async () => {
    mountConfirmHost()
    const firstResolved = vi.fn()

    // Both are called before either is on screen, so both land in the root queue.
    void dialogConfirm({ title: 'first' }).then(firstResolved)
    const second = dialogConfirm({ title: 'second' })
    await flushPromises()

    expect(dialogTitles()).toEqual(['first'])

    clickRole('primary')
    await flushPromises()

    expect(firstResolved).toHaveBeenCalledTimes(1)
    expect(dialogTitles()).toEqual(['second'])

    clickRole('cancel')
    await expect(second).resolves.toBe(false)
    expect(dialogTitles()).toEqual([])
  })

  it('nests a confirm called while a dialog is open under that dialog, with no caller hint', async () => {
    mountConfirmHost()
    const parent = mountShell()
    await flushPromises()

    const parentEntry = dialogStackEntries()[0]
    void dialogConfirm({ title: 'Revert the note?' })
    await flushPromises()

    // Two dialogs on the stack: the feature dialog and the confirm sitting on top of it.
    expect(dialogStackEntries()).toHaveLength(2)
    const confirmEntry = dialogStackEntries()[1]
    expect(confirmEntry.context).toBe(parentEntry)
    expect(confirmEntry.request?.context).toBe(parentEntry)

    // ESC belongs to the nested confirm; the parent is untouched and stays open.
    pressEscape()
    await flushPromises()
    expect(parent.emitted('request-close')).toBeUndefined()
    expect(dialogStackEntries()).toHaveLength(1)
    expect(dialogStackEntries()[0]).toBe(parentEntry)
  })

  it('does not make a nested confirm wait behind the root queue', async () => {
    mountConfirmHost()

    // A root confirm is on screen; a confirm raised from inside it is its child and is
    // displayed immediately instead of queueing behind it.
    void dialogConfirm({ title: 'root' })
    await flushPromises()
    void dialogConfirm({ title: 'nested' })
    await flushPromises()

    expect(dialogTitles()).toEqual(['root', 'nested'])
    expect(dialogStackEntries()).toHaveLength(2)

    clickRole('cancel', 1)
    await flushPromises()
    expect(dialogTitles()).toEqual(['root'])
  })

  it('runs a nested context queue FIFO and hands the dialog back when it drains', async () => {
    mountConfirmHost()
    mountShell({ ariaLabel: 'parent' })
    await flushPromises()
    const parentEntry = dialogStackEntries()[0]

    // Both calls happen while the feature dialog is the active one, so both land in
    // that dialog's own queue rather than in the root queue.
    void dialogConfirm({ title: 'queued A' })
    const b = dialogConfirm({ title: 'queued B' })
    await flushPromises()
    expect(dialogTitles()).toEqual(['queued A'])

    clickRole('primary')
    await flushPromises()
    // The next one takes its place only after the first is answered.
    expect(dialogTitles()).toEqual(['queued B'])

    clickRole('cancel')
    await expect(b).resolves.toBe(false)
    expect(dialogTitles()).toEqual([])
    // The feature dialog is untouched by any of it and is active again.
    expect(dialogStackEntries()).toEqual([parentEntry])
  })
})

describe('imperative confirm() — teardown (완료조건 20)', () => {
  it('flushes the displayed and the still-queued request when the parent disappears', async () => {
    mountConfirmHost()
    const parent = mountShell()
    await flushPromises()

    const active = dialogConfirm({ title: 'active' })
    const pending = dialogConfirm({ title: 'pending' })
    await flushPromises()
    expect(dialogTitles()).toEqual(['active'])

    parent.unmount()
    await flushPromises()

    await expect(active).resolves.toBe(false)
    await expect(pending).resolves.toBe(false)
    // Nothing was re-displayed on the way out.
    expect(dialogTitles()).toEqual([])
    expect(displayedDialogRequests()).toHaveLength(0)
    expect(dialogStackEntries()).toHaveLength(0)
  })

  it('resolves immediately without queueing when the context is already disposing', async () => {
    mountConfirmHost()
    mountShell()
    await flushPromises()

    // Stand in for the teardown window: the entry is still the active dialog but has
    // already been claimed by disposeDialog.
    dialogStackEntries()[0].cleanupState = 'disposing'

    const result = dialogConfirm({ title: 'too late' })
    await flushPromises()

    expect(displayedDialogRequests()).toHaveLength(0)
    expect(dialogTitles()).toEqual([])
    await expect(result).resolves.toBe(false)
  })

  it('keeps a user answer even when teardown re-resolves the same request', async () => {
    mountConfirmHost()
    const parent = mountShell()
    await flushPromises()

    const resolved = vi.fn()
    void dialogConfirm({ title: 'answered' }).then(resolved)
    await flushPromises()

    clickRole('primary')
    await flushPromises()
    expect(resolved).toHaveBeenCalledTimes(1)
    expect(resolved).toHaveBeenCalledWith(true)

    parent.unmount()
    await flushPromises()

    // The flush ran over a request that was already settled; the answer stands and the
    // promise is not resolved a second time.
    expect(resolved).toHaveBeenCalledTimes(1)
    expect(resolved).toHaveBeenCalledWith(true)
  })
})

describe('imperative confirm() — lone forced teardown of the displayed entry (rejection rework)', () => {
  // finalizeDialogOnce previously only resolved the Promise and dropped the displayed
  // entry; it never released `queue.active`, so a forced teardown of the displayed
  // entry ALONE (not via resolveRequest, not via the parent's disposeConfirmQueue) left
  // the context's queue permanently blocked behind an already-resolved request.
  it('root queue: releases queue.active and shows the next pending request', async () => {
    mountConfirmHost()

    const active = dialogConfirm({ title: 'active' })
    const pending = dialogConfirm({ title: 'pending' })
    await flushPromises()
    expect(dialogTitles()).toEqual(['active'])

    // Simulate DialogShell's onBeforeUnmount → forceCleanup firing for just this one
    // displayed entry, e.g. host unmount of a single child / HMR — the parent context
    // (root, here) is never touched.
    const activeEntry = dialogStackEntries()[0]
    expect(activeEntry.request).toBeDefined()
    forceCleanup(activeEntry)
    await flushPromises()

    await expect(active).resolves.toBe(false)
    // Not stuck behind the resolved-but-still-"active" request: pending is now shown.
    expect(dialogTitles()).toEqual(['pending'])

    clickRole('primary')
    await expect(pending).resolves.toBe(true)
    expect(dialogTitles()).toEqual([])

    // The queue keeps working afterwards — a fresh call is not blocked either.
    const after = dialogConfirm({ title: 'after' })
    await flushPromises()
    expect(dialogTitles()).toEqual(['after'])
    clickRole('cancel')
    await expect(after).resolves.toBe(false)
  })

  it('nested queue: releases queue.active and shows the next pending request under the same parent', async () => {
    mountConfirmHost()
    const parent = mountShell()
    await flushPromises()
    const parentEntry = dialogStackEntries()[0]

    const active = dialogConfirm({ title: 'nested active' })
    const pending = dialogConfirm({ title: 'nested pending' })
    await flushPromises()
    expect(dialogTitles()).toEqual(['nested active'])

    const activeEntry = dialogStackEntries()[1]
    expect(activeEntry.context).toBe(parentEntry)
    forceCleanup(activeEntry)
    await flushPromises()

    await expect(active).resolves.toBe(false)
    expect(dialogTitles()).toEqual(['nested pending'])
    // The parent dialog itself was never touched by the lone child teardown.
    expect(parent.emitted('closed')).toBeUndefined()
    expect(dialogStackEntries()).toContain(parentEntry)

    clickRole('cancel')
    await expect(pending).resolves.toBe(false)
    expect(dialogTitles()).toEqual([])
  })
})

describe('imperative alert() (L0009 §2 AlertDialog)', () => {
  it('resolves undefined when closed', async () => {
    mountAlertHost()
    const resolved = vi.fn()
    void dialogAlert({ title: 'Copy failed', tone: 'warning' }).then(resolved)
    await flushPromises()

    expect(dialogTitles()).toEqual(['Copy failed'])
    clickRole('primary')
    await flushPromises()

    expect(resolved).toHaveBeenCalledTimes(1)
    expect(resolved).toHaveBeenCalledWith(undefined)
  })

  it('resolves undefined — never rejects — when a queue flush cancels it', async () => {
    mountAlertHost()
    const parent = mountShell()
    await flushPromises()

    const pending = dialogAlert({ title: 'flushed' })
    await flushPromises()

    parent.unmount()
    await flushPromises()

    await expect(pending).resolves.toBeUndefined()
  })

  it('shares the context rule with confirm, and the two hosts render their own kind', async () => {
    mountConfirmHost()
    mountAlertHost()
    mountShell()
    await flushPromises()

    void dialogAlert({ title: 'nested alert' })
    await flushPromises()

    expect(dialogStackEntries()).toHaveLength(2)
    expect(dialogTitles()).toEqual(['nested alert'])
    expect(displayedDialogRequests().map((request) => request.kind)).toEqual(['alert'])
  })
})
