import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Textarea from '@/components/ui/Textarea.vue'

// T0004 §5.3/§9: value binding, change/update, disabled, readonly, keyboard interaction.
describe('components/ui/Textarea (T0004)', () => {
  it('renders the bound value and placeholder', () => {
    const wrapper = mount(Textarea, { props: { modelValue: 'notes', placeholder: 'Write…' } })

    const el = wrapper.get('textarea')
    expect((el.element as HTMLTextAreaElement).value).toBe('notes')
    expect(el.attributes('placeholder')).toBe('Write…')
  })

  it('emits update:modelValue when typed into', async () => {
    const wrapper = mount(Textarea, { props: { modelValue: '' } })

    await wrapper.get('textarea').setValue('line one')

    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['line one'])
  })

  it('supports disabled and readonly states', () => {
    const disabled = mount(Textarea, { props: { disabled: true } })
    expect(disabled.get('textarea').attributes('disabled')).toBeDefined()

    const readonly = mount(Textarea, { props: { readonly: true } })
    expect(readonly.get('textarea').attributes('readonly')).toBeDefined()
  })

  it('applies the requested rows', () => {
    const wrapper = mount(Textarea, { props: { rows: 8 } })

    expect(wrapper.get('textarea').attributes('rows')).toBe('8')
  })

  it('marks invalid state with aria-invalid', () => {
    const wrapper = mount(Textarea, { props: { invalid: true } })

    expect(wrapper.get('textarea').attributes('aria-invalid')).toBe('true')
  })
})
