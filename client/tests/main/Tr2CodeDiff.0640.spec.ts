import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import Tr2CodeDiff from '@main/components/documents/Tr2CodeDiff.vue'

describe('0640 literal code comparison', () => {
  it('preserves indentation, empty lines and trailing newline and escapes markup', () => {
    const before = '  before\n\tindented\n\n'
    const after = '<img src=x onerror="alert(1)">\n  after\n'
    const w = mount(Tr2CodeDiff, { props: { before, after }, global: { plugins: [i18n] } })
    expect(w.findAll('.code-before code').map(line => line.element.textContent).join('\n')).toBe(before)
    expect(w.findAll('.code-after code').map(line => line.element.textContent).join('\n')).toBe(after)
    expect(w.find('img').exists()).toBe(false)
    expect(w.findAll('.code-scroll').every(panel => panel.attributes('tabindex') === '0')).toBe(true)
    w.unmount()
  })
  it('shows only new content for CREATE', () => {
    const w = mount(Tr2CodeDiff, { props: { after: 'new\n' }, global: { plugins: [i18n] } })
    expect(w.find('.code-before').exists()).toBe(false)
    expect(w.findAll('.tr2-code-block')).toHaveLength(1)
    expect(w.findAll('.code-after code').map(line => line.element.textContent).join('\n')).toBe('new\n')
    w.unmount()
  })
})
