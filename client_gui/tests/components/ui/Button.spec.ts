import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Button from '@/components/ui/Button.vue'

// T0004 §5.1/§9: domain-free button primitive — render, click, disabled, keyboard focus.
describe('components/ui/Button (T0004)', () => {
  it('renders slot content as a real <button> with default variant/size classes', () => {
    const wrapper = mount(Button, { slots: { default: 'Save' } })

    const el = wrapper.get('button')
    expect(el.text()).toBe('Save')
    expect(el.attributes('type')).toBe('button')
    expect(el.classes()).toContain('ui-btn--secondary')
    expect(el.classes()).toContain('ui-btn--md')
  })

  it('applies the requested variant and size classes', () => {
    const wrapper = mount(Button, { props: { variant: 'primary', size: 'lg' }, slots: { default: 'Go' } })

    expect(wrapper.classes()).toContain('ui-btn--primary')
    expect(wrapper.classes()).toContain('ui-btn--lg')
  })

  it('emits a click when enabled', async () => {
    const wrapper = mount(Button, { slots: { default: 'Click' } })

    await wrapper.trigger('click')

    expect(wrapper.emitted('click')).toHaveLength(1)
  })

  it('is disabled and does not emit click when disabled', async () => {
    const wrapper = mount(Button, { props: { disabled: true }, slots: { default: 'Click' } })

    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('is disabled while loading, and shows a busy state', () => {
    const wrapper = mount(Button, { props: { loading: true }, slots: { default: 'Save' } })

    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    expect(wrapper.get('button').attributes('aria-busy')).toBe('true')
    expect(wrapper.find('.ui-btn-spinner').exists()).toBe(true)
  })

  it('is keyboard-focusable when enabled (no explicit tabindex removed)', () => {
    const wrapper = mount(Button, { slots: { default: 'Focus me' } })

    expect(wrapper.get('button').attributes('tabindex')).toBeUndefined()
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
  })

  it('forwards the submit type', () => {
    const wrapper = mount(Button, { props: { type: 'submit' }, slots: { default: 'Submit' } })

    expect(wrapper.get('button').attributes('type')).toBe('submit')
  })
})
