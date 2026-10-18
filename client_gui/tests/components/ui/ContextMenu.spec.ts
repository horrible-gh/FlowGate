import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import ContextMenu from '@/components/ui/ContextMenu.vue'
import ModalShell from '@/components/ui/ModalShell.vue'

// Also clean up after an assertion fails, before removing Teleport nodes.
enableAutoUnmount(afterEach)

// T0010 §8/§13.3: contextmenu-event open, pointer-position placement, viewport clamp,
// outside click / ESC close, keyboard nav, first-item focus, focus return, Teleport,
// shared z-index, and no conflict with ModalShell's focus trap when opened inside one.
describe('components/ui/ContextMenu (T0010)', () => {
  afterEach(() => {
    document.body.querySelectorAll('.ui-contextmenu-menu, .ui-modal-overlay').forEach((el) => el.remove())
  })

  const items = [
    { key: 'rename', label: 'Rename' },
    { key: 'disabled-item', label: 'Locked', disabled: true },
    { key: 'delete', label: 'Delete', danger: true },
  ]

  function mountMenu(props: Record<string, unknown> = {}) {
    return mount(ContextMenu, {
      props: { items, ...props },
      slots: { default: '<div class="target">right-click me</div>' },
      attachTo: document.body,
    })
  }

  it('does not render the menu until opened', () => {
    const wrapper = mountMenu()
    expect(document.querySelector('.ui-contextmenu-menu')).toBeNull()
    wrapper.unmount()
  })

  it('opens on the native contextmenu event and prevents the browser default', async () => {
    const wrapper = mountMenu()
    const target = wrapper.get('.target')
    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 50, clientY: 60 })
    target.element.dispatchEvent(event)
    await wrapper.vm.$nextTick()

    expect(event.defaultPrevented).toBe(true)
    expect(document.querySelector('.ui-contextmenu-menu')).not.toBeNull()
    wrapper.unmount()
  })

  it('positions the menu at the pointer coordinates', async () => {
    const wrapper = mountMenu()
    wrapper.get('.target').element.dispatchEvent(
      new MouseEvent('contextmenu', { bubbles: true, clientX: 123, clientY: 77 }),
    )
    await wrapper.vm.$nextTick()

    const menu = document.querySelector('.ui-contextmenu-menu') as HTMLElement
    expect(menu.style.left).toBe('123px')
    expect(menu.style.top).toBe('77px')
    wrapper.unmount()
  })

  it('clamps position so the menu does not overflow the viewport', async () => {
    const wrapper = mountMenu()
    const menuEl = () => document.querySelector('.ui-contextmenu-menu') as HTMLElement

    wrapper.get('.target').element.dispatchEvent(
      new MouseEvent('contextmenu', { bubbles: true, clientX: 5000, clientY: 5000 }),
    )
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    Object.defineProperty(menuEl(), 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ width: 200, height: 150, top: 0, left: 0, right: 200, bottom: 150 }),
    })
    // Re-open to trigger positioning against the stubbed rect. Clamping happens in a
    // nextTick callback that itself writes a ref, so the write only reaches the DOM on
    // the tick after that -- two awaits, matching how a real re-render settles.
    wrapper.get('.target').element.dispatchEvent(
      new MouseEvent('contextmenu', { bubbles: true, clientX: 5000, clientY: 5000 }),
    )
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(parseFloat(menuEl().style.left)).toBeLessThanOrEqual(window.innerWidth - 200)
    expect(parseFloat(menuEl().style.top)).toBeLessThanOrEqual(window.innerHeight - 150)
    wrapper.unmount()
  })

  it('closes on outside click', async () => {
    const wrapper = mountMenu()
    wrapper.get('.target').element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }))
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.ui-contextmenu-menu')).not.toBeNull()

    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.ui-contextmenu-menu')).toBeNull()
    wrapper.unmount()
  })

  it('closes on Escape', async () => {
    const wrapper = mountMenu()
    wrapper.get('.target').element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }))
    await wrapper.vm.$nextTick()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.ui-contextmenu-menu')).toBeNull()
    wrapper.unmount()
  })

  it('disables the disabled item: not clickable, not selectable, not a keyboard nav stop', async () => {
    const wrapper = mountMenu()
    wrapper.get('.target').element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }))
    await wrapper.vm.$nextTick()

    const disabledBtn = Array.from(document.querySelectorAll('.ui-contextmenu-item')).find(
      (el) => el.textContent?.trim() === 'Locked',
    ) as HTMLButtonElement
    expect(disabledBtn.disabled).toBe(true)

    disabledBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('select')).toBeUndefined()
    wrapper.unmount()
  })

  it('emits select with the chosen item and closes the menu', async () => {
    const wrapper = mountMenu()
    wrapper.get('.target').element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }))
    await wrapper.vm.$nextTick()

    const renameBtn = Array.from(document.querySelectorAll('.ui-contextmenu-item')).find(
      (el) => el.textContent?.trim() === 'Rename',
    ) as HTMLButtonElement
    renameBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('select')?.[0][0]).toEqual({ key: 'rename', label: 'Rename' })
    expect(document.querySelector('.ui-contextmenu-menu')).toBeNull()
    wrapper.unmount()
  })

  it('moves focus to the first (non-disabled) item on open', async () => {
    const wrapper = mountMenu()
    wrapper.get('.target').element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }))
    await wrapper.vm.$nextTick()

    expect(document.activeElement?.textContent?.trim()).toBe('Rename')
    wrapper.unmount()
  })

  it('navigates items with ArrowDown/ArrowUp, skipping the disabled item', async () => {
    const wrapper = mountMenu()
    wrapper.get('.target').element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }))
    await wrapper.vm.$nextTick()

    const menu = document.querySelector('.ui-contextmenu-menu') as HTMLElement
    menu.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
    expect(document.activeElement?.textContent?.trim()).toBe('Delete')

    menu.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }))
    expect(document.activeElement?.textContent?.trim()).toBe('Rename')
    wrapper.unmount()
  })

  it('returns focus to the trigger element when closed', async () => {
    const outside = document.createElement('button')
    outside.textContent = 'origin'
    document.body.appendChild(outside)
    outside.focus()

    const wrapper = mountMenu()
    wrapper.get('.target').element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }))
    await wrapper.vm.$nextTick()
    expect(document.activeElement).not.toBe(outside)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(document.activeElement).toBe(outside)

    wrapper.unmount()
    outside.remove()
  })

  it('is teleported to <body>, not nested under its trigger', async () => {
    const wrapper = mountMenu()
    wrapper.get('.target').element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }))
    await wrapper.vm.$nextTick()

    const menu = document.querySelector('.ui-contextmenu-menu') as HTMLElement
    expect(menu.parentElement).toBe(document.body)
    expect(wrapper.find('.ui-contextmenu-menu').exists()).toBe(false)
    wrapper.unmount()
  })

  it('allocates a z-index higher than a menu opened before it', async () => {
    const first = mountMenu()
    const second = mountMenu()

    first.get('.target').element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 1, clientY: 1 }))
    await first.vm.$nextTick()
    const firstMenu = document.querySelectorAll('.ui-contextmenu-menu')[0] as HTMLElement
    const firstZ = Number(firstMenu.style.zIndex)

    second.get('.target').element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 1, clientY: 1 }))
    await second.vm.$nextTick()
    const secondMenu = document.querySelectorAll('.ui-contextmenu-menu')[1] as HTMLElement

    expect(Number(secondMenu.style.zIndex)).toBeGreaterThan(firstZ)
    first.unmount()
    second.unmount()
  })

  it('opens above an open ModalShell and does not get pulled back into the modal focus trap', async () => {
    const modal = mount(ModalShell, {
      props: { open: true },
      slots: { default: '<div data-modal-autofocus tabindex="-1">modal body</div>' },
      attachTo: document.body,
    })
    await modal.vm.$nextTick()

    const menuWrapper = mount(ContextMenu, {
      props: { items },
      slots: { default: '<div class="target">right-click me</div>' },
      attachTo: document.querySelector('.ui-modal-surface') as HTMLElement,
    })
    menuWrapper.get('.target').element.dispatchEvent(
      new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }),
    )
    await menuWrapper.vm.$nextTick()
    // ModalShell's document focusin guard re-runs asynchronously via nextTick chains in
    // both components; flush one more tick so any (incorrect) yank-back would have landed.
    await menuWrapper.vm.$nextTick()

    const menu = document.querySelector('.ui-contextmenu-menu') as HTMLElement
    const modalSurface = document.querySelector('.ui-modal-surface') as HTMLElement
    expect(Number(menu.style.zIndex)).toBeGreaterThan(Number(modalSurface.style.zIndex))
    expect(menu.dataset.overlayFocusOwner).toBe(modalSurface.dataset.modalFocusScope)
    expect(menu.contains(document.activeElement)).toBe(true)
    expect(modalSurface.contains(document.activeElement)).toBe(false)

    menuWrapper.unmount()
    modal.unmount()
  })

  it('Escape while open inside a ModalShell closes only the menu, not the modal', async () => {
    const modal = mount(ModalShell, {
      props: { open: true },
      slots: { default: '<div data-modal-autofocus tabindex="-1">modal body</div>' },
      attachTo: document.body,
    })
    await modal.vm.$nextTick()

    const menuWrapper = mount(ContextMenu, {
      props: { items },
      slots: { default: '<div class="target">right-click me</div>' },
      attachTo: document.querySelector('.ui-modal-surface') as HTMLElement,
    })
    menuWrapper.get('.target').element.dispatchEvent(
      new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }),
    )
    await menuWrapper.vm.$nextTick()
    await menuWrapper.vm.$nextTick()
    expect(document.querySelector('.ui-contextmenu-menu')).not.toBeNull()

    // A real Escape keypress originates at whatever currently has focus -- the first
    // menu item here -- and bubbles up through <body>/<html>/document, so dispatching
    // it there (rather than directly on `document`) actually exercises capture-vs-
    // bubble ordering between ContextMenu's and ModalShell's document-level Escape
    // handlers instead of plain listener-registration order.
    const focused = document.activeElement as HTMLElement
    focused.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await menuWrapper.vm.$nextTick()

    expect(document.querySelector('.ui-contextmenu-menu')).toBeNull()
    expect(modal.emitted('close')).toBeUndefined()

    menuWrapper.unmount()
    modal.unmount()
  })

  it('preserves the original focus-return target when openAt is invoked again while already open', async () => {
    const outside = document.createElement('button')
    outside.textContent = 'origin'
    document.body.appendChild(outside)
    outside.focus()

    const wrapper = mountMenu()
    wrapper.get('.target').element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }))
    await wrapper.vm.$nextTick()
    expect(document.activeElement?.textContent?.trim()).toBe('Rename')

    // Reposition while still open (e.g. an imperative re-invocation from a different
    // trigger, T0010 §8.3) -- focus is currently on the first menu item, which must
    // NOT overwrite the real focus-return target captured on the first open.
    ;(wrapper.vm as unknown as { openAt: (x: number, y: number) => void }).openAt(40, 40)
    await wrapper.vm.$nextTick()
    expect(document.activeElement?.textContent?.trim()).toBe('Rename')

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(document.activeElement).toBe(outside)

    wrapper.unmount()
    outside.remove()
  })

  it('removes document listeners on unmount (no stray outside-click/escape handling)', async () => {
    const wrapper = mountMenu()
    wrapper.get('.target').element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }))
    await wrapper.vm.$nextTick()
    wrapper.unmount()

    expect(() => {
      document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    }).not.toThrow()
  })
})
