import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Badge from '@/components/ui/Badge.vue'

// T0004 §5.7/§9: label/tone render, no domain-state dependency.
describe('components/ui/Badge (T0004)', () => {
  it('renders the slot label with the default neutral/md tone', () => {
    const wrapper = mount(Badge, { slots: { default: '준비 중' } })

    expect(wrapper.text()).toBe('준비 중')
    expect(wrapper.classes()).toContain('ui-badge--neutral')
    expect(wrapper.classes()).toContain('ui-badge--md')
  })

  it('applies the requested tone and size', () => {
    const wrapper = mount(Badge, { props: { tone: 'danger', size: 'sm' }, slots: { default: 'Conflict' } })

    expect(wrapper.classes()).toContain('ui-badge--danger')
    expect(wrapper.classes()).toContain('ui-badge--sm')
  })

  it('renders an optional icon slot', () => {
    const wrapper = mount(Badge, { slots: { icon: '<svg data-testid="icon" />', default: 'Info' } })

    expect(wrapper.find('[data-testid="icon"]').exists()).toBe(true)
  })

  it('has no props or imports that carry a domain meaning (label/tone are pure presentation)', () => {
    const wrapper = mount(Badge, { props: { tone: 'success' }, slots: { default: 'Done' } })
    // The component receives only its own presentation props — it maps nothing from a
    // Git/Approval/Document status itself, that mapping is the caller's job.
    expect(wrapper.props()).toEqual({ tone: 'success', size: 'md' })
  })
})
