import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { h } from 'vue'
import AlertDialog from '@/components/ui/AlertDialog.vue'

// T0006 §8/§12: acknowledge, ESC/close policy, alertdialog semantic, focus return, no
// Toast behaviour (no auto-dismiss). Rendered content is teleported (via ModalShell) to
// <body>.
describe('components/ui/AlertDialog (T0006)', () => {
  afterEach(() => {
    document.body.querySelectorAll('.ui-modal-overlay').forEach((el) => el.remove())
  })

  function mountAlert(props: Record<string, unknown> = {}) {
    return mount(AlertDialog, {
      props: { open: true, title: 'Session expired', message: 'Please sign in again.', ...props },
      attachTo: document.body,
    })
  }

  it('uses the alertdialog role', () => {
    const wrapper = mountAlert()
    expect(document.querySelector('.ui-modal-surface')?.getAttribute('role')).toBe('alertdialog')
    wrapper.unmount()
  })

  it('renders the caller-supplied title/message and a single acknowledge button defaulting to OK', () => {
    const wrapper = mountAlert()
    expect(document.querySelector('.ui-alert-title')?.textContent).toBe('Session expired')
    expect(document.querySelector('.ui-alert-message')?.textContent).toBe('Please sign in again.')
    const buttons = document.querySelectorAll('.ui-alert-actions .ui-btn')
    expect(buttons).toHaveLength(1)
    expect(buttons[0].textContent?.trim()).toBe('OK')
    wrapper.unmount()
  })

  it('supports a custom acknowledge label', () => {
    const wrapper = mountAlert({ acknowledgeLabel: 'Got it' })
    expect(document.querySelector('.ui-alert-actions .ui-btn')?.textContent?.trim()).toBe('Got it')
    wrapper.unmount()
  })

  it('emits close from the acknowledge button and from Escape, never auto-dismissing on its own', async () => {
    const wrapper = mountAlert()
    const btn = document.querySelector('.ui-alert-actions .ui-btn') as HTMLElement
    btn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(wrapper.emitted('close')).toHaveLength(1)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('close')).toHaveLength(2)
    wrapper.unmount()
  })

  it('applies the requested tone to the message and to the acknowledge button', () => {
    const wrapper = mountAlert({ tone: 'danger' })
    expect(document.querySelector('.ui-alert-message')?.classList.contains('ui-alert-message--danger')).toBe(true)
    expect(document.querySelector('.ui-alert-actions .ui-btn')?.classList.contains('ui-btn--danger')).toBe(true)
    wrapper.unmount()
  })

  it('never closes on a backdrop click', async () => {
    const wrapper = mountAlert()
    const overlay = document.querySelector('.ui-modal-overlay') as HTMLElement
    overlay.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    overlay.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    expect(wrapper.emitted('close')).toBeUndefined()
    wrapper.unmount()
  })

  it('exposes descriptionId to the message slot so structured slot content can wire aria-describedby itself', async () => {
    const wrapper = mount(AlertDialog, {
      props: { open: true, title: 'Session expired' },
      slots: {
        message: (scope: { descriptionId: string }) =>
          h('div', { id: scope.descriptionId, class: 'custom-message' }, 'Please sign in again.'),
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
    const wrapper = mount(AlertDialog, {
      props: { open: true, title: 'Session expired', ariaDescribedby: 'external-desc' },
      attachTo: document.body,
    })
    const surface = document.querySelector('.ui-modal-surface') as HTMLElement
    expect(surface.getAttribute('aria-describedby')).toBe('external-desc')
    wrapper.unmount()
  })
})
