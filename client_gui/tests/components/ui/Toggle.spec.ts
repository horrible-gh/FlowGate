import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Toggle from '@/components/ui/Toggle.vue'

// T0004 §5.5/§9: value binding, change/update, disabled, keyboard interaction.
describe('components/ui/Toggle (T0004)', () => {
  it('renders as an accessible switch reflecting its value', () => {
    const wrapper = mount(Toggle, { props: { modelValue: true, ariaLabel: 'Enabled' } })

    const el = wrapper.get('button')
    expect(el.attributes('role')).toBe('switch')
    expect(el.attributes('aria-checked')).toBe('true')
    expect(el.attributes('aria-label')).toBe('Enabled')
    expect(wrapper.classes()).toContain('is-on')
  })

  it('emits update:modelValue with the flipped value on click', async () => {
    const wrapper = mount(Toggle, { props: { modelValue: false } })

    await wrapper.trigger('click')

    expect(wrapper.emitted('update:modelValue')).toEqual([[true]])
  })

  it('is keyboard-activatable (native button semantics: Enter/Space trigger click)', () => {
    const wrapper = mount(Toggle, { props: { modelValue: false } })

    expect(wrapper.get('button').element.tagName).toBe('BUTTON')
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
  })

  it('does not emit when disabled', async () => {
    const wrapper = mount(Toggle, { props: { modelValue: false, disabled: true } })

    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    await wrapper.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })
})
