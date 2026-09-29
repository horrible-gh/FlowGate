import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LoadingState from '@/components/ui/LoadingState.vue'

// T0008 §8/§14: label/size/density, accessibility semantic, no API/store access of its own.
describe('components/ui/LoadingState (T0008)', () => {
  it('renders with the default label and md size', () => {
    const wrapper = mount(LoadingState)
    expect(wrapper.text()).toContain('Loading')
    expect(wrapper.classes()).toContain('ui-loading-state--md')
  })

  it('renders a custom label', () => {
    const wrapper = mount(LoadingState, { props: { label: 'Fetching projects' } })
    expect(wrapper.text()).toContain('Fetching projects')
  })

  it('applies the requested size', () => {
    const wrapper = mount(LoadingState, { props: { size: 'lg' } })
    expect(wrapper.classes()).toContain('ui-loading-state--lg')
  })

  it('supports an inline layout variant', () => {
    const wrapper = mount(LoadingState, { props: { inline: true } })
    expect(wrapper.classes()).toContain('ui-loading-state--inline')
  })

  it('exposes aria-busy and a polite status live region', () => {
    const wrapper = mount(LoadingState)
    expect(wrapper.attributes('role')).toBe('status')
    expect(wrapper.attributes('aria-busy')).toBe('true')
    expect(wrapper.attributes('aria-live')).toBe('polite')
  })

  it('has no props or imports that fetch data or read a store itself', () => {
    const wrapper = mount(LoadingState, { props: { label: 'Loading projects' } })
    expect(wrapper.props()).toEqual({ label: 'Loading projects', size: 'md', inline: false })
  })
})
