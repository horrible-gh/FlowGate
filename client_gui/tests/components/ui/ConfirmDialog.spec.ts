import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { h } from 'vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'

// T0006 §7/§12: confirm/cancel result, ESC, danger/normal tone, focus order, reuse of
// the shared Button primitive. Rendered content is teleported (via ModalShell) to
// <body>, so DOM assertions look there.
describe('components/ui/ConfirmDialog (T0006)', () => {
  afterEach(() => {
    document.body.querySelectorAll('.ui-modal-overlay').forEach((el) => el.remove())
  })

  function mountConfirm(props: Record<string, unknown> = {}) {
    return mount(ConfirmDialog, {
      props: { open: true, title: 'Delete item?', message: 'This cannot be undone.', ...props },
      attachTo: document.body,
    })
  }

  it('renders the caller-supplied title and message, owning no business text itself', () => {
    const wrapper = mountConfirm()
    expect(document.querySelector('.ui-confirm-title')?.textContent).toBe('Delete item?')
    expect(document.querySelector('.ui-confirm-message')?.textContent).toBe('This cannot be undone.')
    wrapper.unmount()
  })

  it('emits confirm/cancel from the matching buttons, using the shared Button primitive', async () => {
    const wrapper = mountConfirm()
    const buttons = Array.from(document.querySelectorAll('.ui-confirm-actions .ui-btn')) as HTMLElement[]
    expect(buttons).toHaveLength(2)

    buttons[0].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(wrapper.emitted('cancel')).toHaveLength(1)

    buttons[1].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(wrapper.emitted('confirm')).toHaveLength(1)
    wrapper.unmount()
  })

  it('places Cancel before Confirm so the safe action gets initial focus, even in danger mode', async () => {
    const wrapper = mountConfirm({ danger: true })
    await wrapper.vm.$nextTick()
    const buttons = document.querySelectorAll('.ui-confirm-actions .ui-btn')
    expect(buttons[0].textContent).toBe('Cancel')
    expect(buttons[1].textContent).toBe('Confirm')
    expect(document.activeElement).toBe(buttons[0])
    wrapper.unmount()
  })

  it('gives the confirm button danger styling only when danger is true', () => {
    const wrapperNormal = mountConfirm({ danger: false })
    let confirmBtn = document.querySelectorAll('.ui-confirm-actions .ui-btn')[1]
    expect(confirmBtn.classList.contains('ui-btn--danger')).toBe(false)
    wrapperNormal.unmount()

    const wrapperDanger = mountConfirm({ danger: true })
    confirmBtn = document.querySelectorAll('.ui-confirm-actions .ui-btn')[1]
    expect(confirmBtn.classList.contains('ui-btn--danger')).toBe(true)
    wrapperDanger.unmount()
  })

  it('treats Escape as cancel', async () => {
    const wrapper = mountConfirm()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    wrapper.unmount()
  })

  it('supports custom confirm/cancel labels', () => {
    const wrapper = mountConfirm({ confirmLabel: 'Delete', cancelLabel: 'Keep it' })
    const buttons = document.querySelectorAll('.ui-confirm-actions .ui-btn')
    expect(buttons[0].textContent).toBe('Keep it')
    expect(buttons[1].textContent).toBe('Delete')
    wrapper.unmount()
  })

  it('exposes descriptionId to the message slot so structured slot content can wire aria-describedby itself', async () => {
    const wrapper = mount(ConfirmDialog, {
      props: { open: true, title: 'Delete item?' },
      slots: {
        message: (scope: { descriptionId: string }) =>
          h('div', { id: scope.descriptionId, class: 'custom-message' }, [h('strong', 'Warning'), ' this cannot be undone.']),
      },
      attachTo: document.body,
    })
    await wrapper.vm.$nextTick()

    const surface = document.querySelector('.ui-modal-surface') as HTMLElement
    const describedby = surface.getAttribute('aria-describedby')
    expect(describedby).toBeTruthy()
    expect(document.getElementById(describedby as string)?.classList.contains('custom-message')).toBe(true)
    wrapper.unmount()
  })

  it('forwards an explicit ariaDescribedby to ModalShell for message content that cannot own the generated id', () => {
    const wrapper = mount(ConfirmDialog, {
      props: { open: true, title: 'Delete item?', ariaDescribedby: 'external-desc' },
      attachTo: document.body,
    })
    const surface = document.querySelector('.ui-modal-surface') as HTMLElement
    expect(surface.getAttribute('aria-describedby')).toBe('external-desc')
    wrapper.unmount()
  })
})
