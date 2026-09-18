import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'

// flowgate.default.0560 T0012 §2.6 / §5-3 — route-leave confirm.
//
// L0009 §5 "완료 조건" 11: a route-leave confirm is never created twice, and a second
// navigation raised while the first is still being answered is cancelled rather than
// reusing the pending answer (L0009 §2 "Route-leave confirm", §3 상태 전이).
//
// The state machine itself belongs to the feature that owns the route (L0009 names
// `AiProjectSettingsView.vue`), and rewiring that screen is 2순위 — outside this T. What
// this spec pins is the half the common layer owes that feature: one imperative
// `confirm()` produces exactly one dialog, its answer reaches exactly one awaiting
// caller, and a forced teardown resolves false so the guard converges on "stay".
// `routeGuard` below is the L0009 §2 pseudocode transcribed verbatim, so a change in the
// common layer that breaks the pattern fails here instead of in the migration.
import ConfirmDialog from '@main/components/dialogs/ConfirmDialog.vue'
import DialogShell from '@main/components/dialogs/DialogShell.vue'
import { confirm as dialogConfirm, resetDialogSystem } from '@main/composables/useDialogStack'

type RouteState = 'CLEAN' | 'DIRTY' | 'CONFIRMING' | 'LEAVING' | 'STAYING'

interface RouteGuard {
  state: () => RouteState
  markDirty: () => void
  leave: (target: string) => Promise<'allowed' | 'cancelled'>
  allowed: string[]
}

function createRouteGuard(): RouteGuard {
  let state: RouteState = 'CLEAN'
  const allowed: string[] = []

  async function leave(target: string): Promise<'allowed' | 'cancelled'> {
    if (state === 'CLEAN') {
      allowed.push(target)
      return 'allowed'
    }
    if (state === 'CONFIRMING') {
      // Confirmed by L0009 §2: the pending decision belongs to the first request only,
      // so a later navigation is cancelled instead of inheriting an answer the user
      // gave about a different destination.
      return 'cancelled'
    }
    state = 'CONFIRMING'
    const result = await dialogConfirm({ title: 'Leave without saving?' })
    if (result) {
      state = 'LEAVING'
      allowed.push(target)
      return 'allowed'
    }
    state = 'STAYING'
    state = 'DIRTY'
    return 'cancelled'
  }

  return {
    state: () => state,
    markDirty: () => {
      state = 'DIRTY'
    },
    leave,
    allowed,
  }
}

const wrappers: VueWrapper[] = []

function mountConfirmHost(): VueWrapper {
  const wrapper = mount(ConfirmDialog as never, {
    props: { host: true },
    global: { plugins: [i18n] },
    attachTo: document.body,
  })
  wrappers.push(wrapper)
  return wrapper
}

function openDialogs(): number {
  return document.querySelectorAll('.fg-dialog-surface').length
}

function clickRole(role: 'primary' | 'cancel'): void {
  document.querySelector<HTMLElement>(`[data-dialog-action-role="${role}"]`)?.dispatchEvent(
    new MouseEvent('click', { bubbles: true }),
  )
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

describe('route-leave confirm (L0009 §2/§3, 완료조건 11)', () => {
  it('lets a clean screen navigate without asking', async () => {
    mountConfirmHost()
    const guard = createRouteGuard()

    await expect(guard.leave('/settings')).resolves.toBe('allowed')
    expect(openDialogs()).toBe(0)
  })

  it('shows exactly one confirm and cancels a second navigation raised while it is open', async () => {
    mountConfirmHost()
    const guard = createRouteGuard()
    guard.markDirty()

    const first = guard.leave('/settings')
    await flushPromises()
    expect(openDialogs()).toBe(1)
    expect(guard.state()).toBe('CONFIRMING')

    // A second navigation arrives while the user is still deciding.
    await expect(guard.leave('/projects')).resolves.toBe('cancelled')
    await flushPromises()
    // No second dialog was created — and the destination the user never saw is not
    // allowed on the strength of the first answer.
    expect(openDialogs()).toBe(1)

    clickRole('primary')
    await expect(first).resolves.toBe('allowed')
    expect(guard.allowed).toEqual(['/settings'])
    expect(openDialogs()).toBe(0)
  })

  it('returns to DIRTY and stays when the user cancels', async () => {
    mountConfirmHost()
    const guard = createRouteGuard()
    guard.markDirty()

    const leaving = guard.leave('/settings')
    await flushPromises()
    clickRole('cancel')

    await expect(leaving).resolves.toBe('cancelled')
    expect(guard.state()).toBe('DIRTY')
    expect(guard.allowed).toEqual([])

    // Still guarded afterwards: the next attempt asks again rather than inheriting.
    const second = guard.leave('/projects')
    await flushPromises()
    expect(openDialogs()).toBe(1)
    clickRole('primary')
    await expect(second).resolves.toBe('allowed')
  })

  it('converges on staying when the confirm is flushed by a teardown', async () => {
    mountConfirmHost()
    const parent = mount(DialogShell as never, {
      props: { open: true, variant: 'form', ariaLabel: 'owner' },
      global: { plugins: [i18n] },
      attachTo: document.body,
    })
    wrappers.push(parent)
    await flushPromises()

    const guard = createRouteGuard()
    guard.markDirty()
    const leaving = guard.leave('/settings')
    await flushPromises()

    // The screen that raised the confirm disappears mid-question.
    parent.unmount()
    await flushPromises()

    await expect(leaving).resolves.toBe('cancelled')
    expect(guard.state()).toBe('DIRTY')
    expect(guard.allowed).toEqual([])
  })
})
