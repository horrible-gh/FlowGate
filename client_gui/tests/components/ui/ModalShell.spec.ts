import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { h } from 'vue'
import ModalShell from '@/components/ui/ModalShell.vue'

// T0006 §5/§12: Teleport, backdrop, ESC, backdrop-close opt-in, focus trap, focus
// return, role/aria-modal, title/description accessibility connection. Rendered content
// is teleported to <body>, so it is looked up on document rather than inside the
// mounted wrapper (same pattern as Dropdown/Tooltip in T0004).
describe('components/ui/ModalShell (T0006)', () => {
  afterEach(() => {
    document.body.querySelectorAll('.ui-modal-overlay').forEach((el) => el.remove())
    document.body.innerHTML = ''
  })

  function mountModal(props: Record<string, unknown> = {}, slots: Record<string, unknown> = {}) {
    return mount(ModalShell, {
      props: { open: true, ...props },
      slots: {
        header: (scope: { titleId: string }) => h('h2', { id: scope.titleId }, 'Title'),
        default: () => [h('button', { class: 'field-a' }, 'A'), h('button', { class: 'field-b' }, 'B')],
        ...slots,
      },
      attachTo: document.body,
    })
  }

  it('renders nothing when closed and teleports the surface to <body> when open', async () => {
    const wrapper = mount(ModalShell, { props: { open: false }, attachTo: document.body })
    expect(document.querySelector('.ui-modal-overlay')).toBeNull()

    await wrapper.setProps({ open: true })
    expect(document.querySelector('.ui-modal-overlay')).not.toBeNull()
    expect(document.querySelector('.ui-modal-surface')).not.toBeNull()

    wrapper.unmount()
  })

  it('carries the required dialog ARIA contract', () => {
    const wrapper = mountModal()
    const surface = document.querySelector('.ui-modal-surface') as HTMLElement
    expect(surface.getAttribute('role')).toBe('dialog')
    expect(surface.getAttribute('aria-modal')).toBe('true')
    wrapper.unmount()
  })

  it('connects aria-labelledby to the header slot title id it generated', async () => {
    const wrapper = mountModal()
    await wrapper.vm.$nextTick()
    const surface = document.querySelector('.ui-modal-surface') as HTMLElement
    const titleId = surface.getAttribute('aria-labelledby')
    expect(titleId).toBeTruthy()
    expect(document.getElementById(titleId as string)?.textContent).toBe('Title')
    wrapper.unmount()
  })

  it('applies the requested size class', () => {
    const wrapper = mountModal({ size: 'lg' })
    expect(document.querySelector('.ui-modal-surface')?.classList.contains('ui-modal-surface--lg')).toBe(true)
    wrapper.unmount()
  })

  it('closes on Escape when closeOnEscape is true (default)', async () => {
    const wrapper = mountModal()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toEqual([['escape']])
    wrapper.unmount()
  })

  it('does not close on Escape when closeOnEscape is false', async () => {
    const wrapper = mountModal({ closeOnEscape: false })
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toBeUndefined()
    wrapper.unmount()
  })

  it('does not close on a backdrop click by default (closeOnBackdrop is opt-in)', async () => {
    const wrapper = mountModal()
    const overlay = document.querySelector('.ui-modal-overlay') as HTMLElement
    overlay.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    overlay.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    expect(wrapper.emitted('close')).toBeUndefined()
    wrapper.unmount()
  })

  it('closes on a backdrop click when closeOnBackdrop is true', async () => {
    const wrapper = mountModal({ closeOnBackdrop: true })
    const overlay = document.querySelector('.ui-modal-overlay') as HTMLElement
    // dispatchEvent sets event.target to the element it was dispatched on.
    overlay.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    overlay.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toEqual([['backdrop']])
    wrapper.unmount()
  })

  it('does not close on backdrop when a press started inside the surface is released on the overlay', async () => {
    const wrapper = mountModal({ closeOnBackdrop: true })
    const surface = document.querySelector('.ui-modal-surface') as HTMLElement
    const overlay = document.querySelector('.ui-modal-overlay') as HTMLElement
    surface.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    overlay.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    expect(wrapper.emitted('close')).toBeUndefined()
    wrapper.unmount()
  })

  it('moves initial focus to the first focusable element inside the surface on open', async () => {
    const wrapper = mountModal()
    await wrapper.vm.$nextTick()
    expect(document.activeElement?.className).toBe('field-a')
    wrapper.unmount()
  })

  it('honours an explicit [data-modal-autofocus] target over the first focusable element', async () => {
    const wrapper = mount(ModalShell, {
      props: { open: true },
      slots: {
        default: h('div', [
          h('button', { class: 'field-a' }, 'A'),
          h('button', { class: 'field-b', 'data-modal-autofocus': '' }, 'B'),
        ]),
      },
      attachTo: document.body,
    })
    await wrapper.vm.$nextTick()
    expect(document.activeElement?.className).toBe('field-b')
    wrapper.unmount()
  })

  it('traps Tab focus inside the surface, wrapping from the last field back to the first', async () => {
    const wrapper = mountModal()
    await wrapper.vm.$nextTick()

    const b = document.querySelector('.field-b') as HTMLElement
    b.focus()
    const surface = document.querySelector('.ui-modal-surface') as HTMLElement
    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    surface.dispatchEvent(event)

    expect(document.activeElement?.className).toBe('field-a')
    wrapper.unmount()
  })

  it('traps Shift+Tab focus, wrapping from the first field back to the last', async () => {
    const wrapper = mountModal()
    await wrapper.vm.$nextTick()

    const a = document.querySelector('.field-a') as HTMLElement
    a.focus()
    const surface = document.querySelector('.ui-modal-surface') as HTMLElement
    const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })
    surface.dispatchEvent(event)

    expect(document.activeElement?.className).toBe('field-b')
    wrapper.unmount()
  })

  it('mints unique ids across two ModalShell instances open at the same time', async () => {
    const a = mountModal()
    const b = mountModal()
    await a.vm.$nextTick()
    await b.vm.$nextTick()

    const surfaces = document.querySelectorAll('.ui-modal-surface')
    expect(surfaces).toHaveLength(2)
    const labelledbyA = surfaces[0].getAttribute('aria-labelledby')
    const labelledbyB = surfaces[1].getAttribute('aria-labelledby')
    expect(labelledbyA).toBeTruthy()
    expect(labelledbyB).toBeTruthy()
    expect(labelledbyA).not.toBe(labelledbyB)

    a.unmount()
    b.unmount()
  })

  it('keeps the body scroll locked while a later-opened modal remains open, even if the first-opened one closes first', async () => {
    const first = mountModal()
    await first.vm.$nextTick()
    const second = mountModal()
    await second.vm.$nextTick()
    expect(document.body.style.overflow).toBe('hidden')

    await first.setProps({ open: false })
    await first.vm.$nextTick()
    expect(document.body.style.overflow).toBe('hidden')

    await second.setProps({ open: false })
    await second.vm.$nextTick()
    expect(document.body.style.overflow).toBe('')

    first.unmount()
    second.unmount()
  })

  it('lets only the topmost modal handle Escape when two are open at once', async () => {
    const first = mountModal()
    await first.vm.$nextTick()
    const second = mountModal()
    await second.vm.$nextTick()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await second.vm.$nextTick()

    expect(second.emitted('close')).toEqual([['escape']])
    expect(first.emitted('close')).toBeUndefined()

    first.unmount()
    second.unmount()
  })

  it('only the topmost modal pulls stray focus back into itself when two are open', async () => {
    const outside = document.createElement('button')
    outside.className = 'outside-trigger'
    document.body.appendChild(outside)

    const first = mountModal()
    await first.vm.$nextTick()
    const second = mountModal()
    await second.vm.$nextTick()

    outside.focus()
    await second.vm.$nextTick()

    const secondSurface = document.querySelectorAll('.ui-modal-surface')[1] as HTMLElement
    expect(secondSurface.contains(document.activeElement)).toBe(true)

    first.unmount()
    second.unmount()
    outside.remove()
  })

  it('returns focus to the trigger element once closed', async () => {
    const trigger = document.createElement('button')
    trigger.className = 'trigger'
    document.body.appendChild(trigger)
    trigger.focus()

    const wrapper = mount(ModalShell, { props: { open: false }, attachTo: document.body })
    await wrapper.setProps({ open: true })
    await wrapper.vm.$nextTick()
    expect(document.activeElement).not.toBe(trigger)

    await wrapper.setProps({ open: false })
    await wrapper.vm.$nextTick()
    expect(document.activeElement).toBe(trigger)

    wrapper.unmount()
    trigger.remove()
  })
})
