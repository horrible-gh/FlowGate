import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ErrorState from '@/components/ui/ErrorState.vue'

// T0008 §9/§14: title/message, details/action(retry) slots owned by the caller, tone/
// severity, no axios/HTTP/router/store logic of its own.
describe('components/ui/ErrorState (T0008)', () => {
  it('renders the caller-supplied title and message with the default danger tone', () => {
    const wrapper = mount(ErrorState, { props: { title: 'Failed to load', message: 'Please try again.' } })
    expect(wrapper.find('.ui-error-state-title').text()).toBe('Failed to load')
    expect(wrapper.find('.ui-error-state-message').text()).toBe('Please try again.')
    expect(wrapper.classes()).toContain('ui-error-state--danger')
  })

  it('applies the warning tone', () => {
    const wrapper = mount(ErrorState, { props: { title: 'Partial result', tone: 'warning' } })
    expect(wrapper.classes()).toContain('ui-error-state--warning')
  })

  it('renders optional details and action/retry slots, both owned by the caller', () => {
    const wrapper = mount(ErrorState, {
      props: { title: 'Failed to load' },
      slots: {
        details: '<pre class="raw-details">Timeout</pre>',
        action: '<button class="retry-btn">Retry</button>',
      },
    })
    expect(wrapper.find('.raw-details').exists()).toBe(true)
    expect(wrapper.find('.retry-btn').exists()).toBe(true)
  })

  it('applies compact density', () => {
    const wrapper = mount(ErrorState, { props: { title: 'Failed to load', compact: true } })
    expect(wrapper.classes()).toContain('ui-error-state--compact')
  })

  it('is announced via role="alert"', () => {
    const wrapper = mount(ErrorState, { props: { title: 'Failed to load' } })
    expect(wrapper.attributes('role')).toBe('alert')
  })

  it('has no props for API/router/store access -- retry stays entirely the caller\'s action slot', () => {
    const wrapper = mount(ErrorState, { props: { title: 'Failed to load' } })
    expect(wrapper.props()).toEqual({ title: 'Failed to load', message: undefined, tone: 'danger', compact: false })
  })
})
