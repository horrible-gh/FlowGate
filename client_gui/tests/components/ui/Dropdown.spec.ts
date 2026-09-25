import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import Dropdown from '@/components/ui/Dropdown.vue'

// T0004 §5.9/§9: open/close, outside click, ESC, keyboard/focus. The menu is teleported to
// <body>, so it is looked up on document rather than inside the mounted wrapper.
describe('components/ui/Dropdown (T0004)', () => {
  afterEach(() => {
    document.body.querySelectorAll('.ui-dropdown-menu').forEach((el) => el.remove())
  })

  // Documented usage (T0004 §6): the caller wires the scoped `toggle`/`triggerAttrs` onto its
  // own trigger element via `#trigger="{ toggle, triggerAttrs }"`. Dropdown itself must never
  // also listen for click on the wrapper, or this same click would fire toggle() twice.
  function mountDropdown() {
    return mount(Dropdown, {
      slots: {
        trigger: `<template #trigger="{ toggle, triggerAttrs }">
          <button class="trigger" v-bind="triggerAttrs" @click="toggle">Open</button>
        </template>`,
        default: '<button role="menuitem" class="item-a">A</button><button role="menuitem" class="item-b">B</button>',
      },
      attachTo: document.body,
    })
  }

  it('opens the menu on a single trigger click via the scoped toggle and closes on a second click', async () => {
    const wrapper = mountDropdown()

    expect(document.querySelector('.ui-dropdown-menu')).toBeNull()

    await wrapper.get('.trigger').trigger('click')
    expect(document.querySelector('.ui-dropdown-menu')).not.toBeNull()
    expect(document.querySelectorAll('.item-a')).toHaveLength(1)

    await wrapper.get('.trigger').trigger('click')
    expect(document.querySelector('.ui-dropdown-menu')).toBeNull()

    wrapper.unmount()
  })

  it('closes on outside click', async () => {
    const wrapper = mountDropdown()
    await wrapper.get('.trigger').trigger('click')
    expect(document.querySelector('.ui-dropdown-menu')).not.toBeNull()

    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.ui-dropdown-menu')).toBeNull()

    wrapper.unmount()
  })

  it('closes on Escape and returns focus to the trigger', async () => {
    const wrapper = mountDropdown()
    await wrapper.get('.trigger').trigger('click')
    expect(document.querySelector('.ui-dropdown-menu')).not.toBeNull()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.ui-dropdown-menu')).toBeNull()
    expect(document.activeElement).toBe(wrapper.get('.trigger').element)

    wrapper.unmount()
  })

  it('moves focus into the first menu item on open (keyboard focus)', async () => {
    const wrapper = mountDropdown()
    await wrapper.get('.trigger').trigger('click')
    await wrapper.vm.$nextTick()

    expect(document.activeElement?.className).toBe('item-a')

    wrapper.unmount()
  })

  it('carries no Project/Git/AI menu content of its own — items come entirely from the slot', () => {
    const wrapper = mountDropdown()
    expect(Object.keys(wrapper.props())).toHaveLength(0)
  })

  it('does not double-toggle when the caller binds the scoped toggle directly to trigger click (single click ownership)', async () => {
    const wrapper = mountDropdown()

    // A single click bubbles from the <button> through Dropdown's wrapper span. If the
    // wrapper also listened for click, toggle() would run twice and the menu would end up
    // closed again in the same tick — this asserts it stays open after exactly one click.
    await wrapper.get('.trigger').trigger('click')
    expect(document.querySelector('.ui-dropdown-menu')).not.toBeNull()

    wrapper.unmount()
  })

  it('exposes aria-haspopup/aria-expanded on the trigger via the scoped slot', async () => {
    const wrapper = mountDropdown()

    const trigger = wrapper.get('.trigger')
    expect(trigger.attributes('aria-haspopup')).toBe('menu')
    expect(trigger.attributes('aria-expanded')).toBe('false')

    await trigger.trigger('click')
    expect(wrapper.get('.trigger').attributes('aria-expanded')).toBe('true')

    wrapper.unmount()
  })
})
