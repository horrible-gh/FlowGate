import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import EmptyState from '@/components/ui/EmptyState.vue'

// T0008 §7/§14: title/description, icon/action slots, compact density, no domain wording
// of its own -- everything shown comes from the caller.
describe('components/ui/EmptyState (T0008)', () => {
  it('renders the caller-supplied title', () => {
    const wrapper = mount(EmptyState, { props: { title: 'No items yet' } })
    expect(wrapper.find('.ui-empty-state-title').text()).toBe('No items yet')
  })

  it('renders an optional description', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'No items yet', description: 'Create one to get started.' },
    })
    expect(wrapper.find('.ui-empty-state-description').text()).toBe('Create one to get started.')
  })

  it('omits the description paragraph when none is given', () => {
    const wrapper = mount(EmptyState, { props: { title: 'No items yet' } })
    expect(wrapper.find('.ui-empty-state-description').exists()).toBe(false)
  })

  it('renders optional icon and action slots', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'No items yet' },
      slots: { icon: '<svg data-testid="icon" />', action: '<button class="create-btn">Create</button>' },
    })
    expect(wrapper.find('[data-testid="icon"]').exists()).toBe(true)
    expect(wrapper.find('.create-btn').exists()).toBe(true)
  })

  it('applies compact density', () => {
    const wrapper = mount(EmptyState, { props: { title: 'No items yet', compact: true } })
    expect(wrapper.classes()).toContain('ui-empty-state--compact')
  })

  it('has no domain-specific default of its own -- it only ever echoes caller props', () => {
    const wrapper = mount(EmptyState, { props: { title: '프로젝트가 없습니다' } })
    expect(wrapper.props()).toEqual({ title: '프로젝트가 없습니다', description: undefined, compact: false })
  })
})
