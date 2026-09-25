import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Input from '@/components/ui/Input.vue'

// T0004 §5.3/§9: value binding, change/update, disabled, readonly, keyboard interaction.
describe('components/ui/Input (T0004)', () => {
  it('renders the bound value and placeholder', () => {
    const wrapper = mount(Input, { props: { modelValue: 'hello', placeholder: 'Search…' } })

    const el = wrapper.get('input')
    expect((el.element as HTMLInputElement).value).toBe('hello')
    expect(el.attributes('placeholder')).toBe('Search…')
  })

  it('emits update:modelValue when typed into (keyboard interaction)', async () => {
    const wrapper = mount(Input, { props: { modelValue: '' } })

    await wrapper.get('input').setValue('flow')

    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['flow'])
  })

  it('supports disabled and readonly states', () => {
    const disabled = mount(Input, { props: { disabled: true } })
    expect(disabled.get('input').attributes('disabled')).toBeDefined()

    const readonly = mount(Input, { props: { readonly: true } })
    expect(readonly.get('input').attributes('readonly')).toBeDefined()
  })

  it('marks invalid state with aria-invalid', () => {
    const wrapper = mount(Input, { props: { invalid: true } })

    expect(wrapper.get('input').attributes('aria-invalid')).toBe('true')
    expect(wrapper.classes()).toContain('is-invalid')
  })

  it('forwards the type prop', () => {
    const wrapper = mount(Input, { props: { type: 'search' } })

    expect(wrapper.get('input').attributes('type')).toBe('search')
  })
})
