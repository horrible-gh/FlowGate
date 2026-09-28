import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import LineNumberedTextarea from '@main/components/common/LineNumberedTextarea.vue'

function lastLineNumber(wrapper: ReturnType<typeof mount>): string {
  const text = wrapper.get('.line-numbered-textarea__gutter').element.textContent ?? ''
  return text.split('\n').at(-1) ?? ''
}

describe('LineNumberedTextarea (0590)', () => {
  it('renders line numbers as one gutter text node and updates with the model', async () => {
    const wrapper = mount(LineNumberedTextarea, { props: { modelValue: 'a\nb\nc' } })
    const gutter = wrapper.get('.line-numbered-textarea__gutter')

    expect(gutter.element.childElementCount).toBe(0)
    expect(gutter.element.textContent).toBe('1\n2\n3')

    await wrapper.setProps({ modelValue: 'a\nb\nc\nd' })
    expect(lastLineNumber(wrapper)).toBe('4')

    await wrapper.setProps({ modelValue: 'a\nb' })
    expect(lastLineNumber(wrapper)).toBe('2')
  })

  it('emits edits and keeps logical wrapping disabled when requested', async () => {
    const wrapper = mount(LineNumberedTextarea, {
      props: { modelValue: 'one\ntwo', wrapOff: true },
    })
    const textarea = wrapper.get<HTMLTextAreaElement>('textarea')

    expect(textarea.attributes('wrap')).toBe('off')
    await textarea.setValue('one\ntwo\nthree')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['one\ntwo\nthree'])
  })

  it('synchronizes the gutter vertical scroll with the textarea', async () => {
    const wrapper = mount(LineNumberedTextarea, {
      props: { modelValue: Array.from({ length: 200 }, (_, index) => `line ${index + 1}`).join('\n') },
    })
    const textarea = wrapper.get<HTMLTextAreaElement>('textarea')
    const gutter = wrapper.get<HTMLElement>('.line-numbered-textarea__gutter')

    textarea.element.scrollTop = 180
    await textarea.trigger('scroll')
    expect(gutter.element.scrollTop).toBe(180)
  })

  it('does not create one DOM element per line for large inputs', () => {
    const wrapper = mount(LineNumberedTextarea, {
      props: { modelValue: Array.from({ length: 20000 }, (_, index) => `line ${index + 1}`).join('\n') },
    })
    const gutter = wrapper.get('.line-numbered-textarea__gutter')

    expect(lastLineNumber(wrapper)).toBe('20000')
    expect(gutter.element.childElementCount).toBe(0)
  })
})
