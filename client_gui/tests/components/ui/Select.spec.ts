import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Select from '@/components/ui/Select.vue'

const OPTIONS = [
  { value: 'a', label: 'Alpha' },
  { value: 'b', label: 'Beta', disabled: true },
]

// T0004 §5.4/§9: value binding, change/update, disabled, keyboard interaction.
describe('components/ui/Select (T0004)', () => {
  it('renders each option with its label', () => {
    const wrapper = mount(Select, { props: { options: OPTIONS, modelValue: 'a' } })

    const options = wrapper.findAll('option')
    expect(options.map((o) => o.text())).toEqual(['Alpha', 'Beta'])
    expect(options[1].attributes('disabled')).toBeDefined()
  })

  it('shows a disabled placeholder option when given one and nothing is selected', () => {
    const wrapper = mount(Select, { props: { options: OPTIONS, placeholder: 'Choose…' } })

    const first = wrapper.get('option')
    expect(first.text()).toBe('Choose…')
    expect(first.attributes('disabled')).toBeDefined()
  })

  it('emits update:modelValue on change (keyboard-selectable native control)', async () => {
    const wrapper = mount(Select, { props: { options: OPTIONS, modelValue: 'a' } })

    await wrapper.get('select').setValue('a')
    await wrapper.get('select').trigger('change')

    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['a'])
  })

  it('supports a disabled select', () => {
    const wrapper = mount(Select, { props: { options: OPTIONS, disabled: true } })

    expect(wrapper.get('select').attributes('disabled')).toBeDefined()
  })
})
