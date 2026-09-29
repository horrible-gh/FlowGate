import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Alert from '@/components/ui/Alert.vue'

// T0008 §6/§14: tone/title/message, dismissible on/off, action slot, stays mounted on its
// own (no Toast-style auto-dismiss), and is independent from AlertDialog.
describe('components/ui/Alert (T0008)', () => {
  it('renders the default info tone with title and message content', () => {
    const wrapper = mount(Alert, {
      props: { title: 'Heads up' },
      slots: { default: 'Please check your network connection.' },
    })
    expect(wrapper.classes()).toContain('ui-alert--info')
    expect(wrapper.find('.ui-alert-title').text()).toBe('Heads up')
    expect(wrapper.find('.ui-alert-message').text()).toBe('Please check your network connection.')
  })

  it('applies the requested tone', () => {
    const wrapper = mount(Alert, { props: { tone: 'danger' }, slots: { default: 'Failed' } })
    expect(wrapper.classes()).toContain('ui-alert--danger')
  })

  it('omits the title paragraph when no title is given', () => {
    const wrapper = mount(Alert, { slots: { default: 'Just a message' } })
    expect(wrapper.find('.ui-alert-title').exists()).toBe(false)
  })

  it('shows no close control by default, and one when dismissible is true', async () => {
    const notDismissible = mount(Alert, { slots: { default: 'Info' } })
    expect(notDismissible.find('.ui-alert-close').exists()).toBe(false)

    const dismissible = mount(Alert, { props: { dismissible: true }, slots: { default: 'Info' } })
    expect(dismissible.find('.ui-alert-close').exists()).toBe(true)
    await dismissible.find('.ui-alert-close').trigger('click')
    expect(dismissible.emitted('dismiss')).toHaveLength(1)
  })

  it('renders an optional action slot, owned entirely by the caller', () => {
    const wrapper = mount(Alert, {
      slots: { default: 'Network error', action: '<button class="retry-btn">Retry</button>' },
    })
    expect(wrapper.find('.retry-btn').exists()).toBe(true)
  })

  it('stays rendered on its own -- it never auto-removes itself the way Toast does', async () => {
    const wrapper = mount(Alert, { slots: { default: 'Persistent notice' } })
    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(wrapper.classes()).toContain('ui-alert')
  })

  it('never renders as a blocking dialog -- independent of AlertDialog', () => {
    const wrapper = mount(Alert, { slots: { default: 'Info' } })
    expect(wrapper.attributes('aria-modal')).toBeUndefined()
    expect(wrapper.attributes('role')).not.toBe('alertdialog')
  })
})
