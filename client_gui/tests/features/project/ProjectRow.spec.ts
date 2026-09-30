import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ProjectRow from '@/features/project/ProjectRow.vue'
import type { Project } from '@/features/project/types'

// WP0005 T#2 §2.3/§8: current vs selected are two independent, non-color-only signals,
// and the row never nests one interactive element inside another.
function project(overrides: Partial<Project> = {}): Project {
  return {
    id: 'demo',
    name: 'Demo Project',
    description: 'A demo',
    color: '#2563eb',
    isActive: true,
    createdAt: null,
    updatedAt: '2026-09-20T00:00:00+09:00',
    modules: [],
    ...overrides,
  }
}

describe('features/project/ProjectRow', () => {
  it('renders the name, description and a "최종 수정" (never "최근 사용") date', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false, current: false } })
    expect(wrapper.find('.project-row-name').text()).toBe('Demo Project')
    expect(wrapper.find('.project-row-desc').text()).toBe('A demo')
    const updated = wrapper.find('.project-row-updated').text()
    expect(updated).toContain('최종 수정')
    expect(updated).toContain('2026-09-20')
    expect(updated).not.toContain('최근 사용')
    expect(updated).not.toContain('최근 열기')
  })

  it('shows a text Badge for current, not just a color/border change', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false, current: true } })
    expect(wrapper.find('.ui-badge').text()).toBe('현재')
  })

  it('shows a visible checkmark plus aria-pressed on the focusable main control for selected, not color alone', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: true, current: false } })
    expect(wrapper.find('.project-row-main').attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('.project-row-select-mark').text()).toBe('✓')
  })

  it('omits the checkmark and Badge when neither selected nor current', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false, current: false } })
    expect(wrapper.find('.project-row-main').attributes('aria-pressed')).toBe('false')
    expect(wrapper.find('.project-row-select-mark').text()).toBe('')
    expect(wrapper.find('.ui-badge').exists()).toBe(false)
  })

  it('is a plain list item, not an ARIA option widget nesting two interactive descendants', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false, current: false } })
    expect(wrapper.attributes('role')).toBeUndefined()
  })

  it('marks current via aria-current on the focusable main control, not the (non-interactive) list item', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false, current: true } })
    expect(wrapper.attributes('aria-current')).toBeUndefined()
    expect(wrapper.find('.project-row-main').attributes('aria-current')).toBe('true')
  })

  it('emits select on a single click of the main area, not open', async () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false, current: false } })
    await wrapper.find('.project-row-main').trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual([project()])
    expect(wrapper.emitted('open')).toBeUndefined()
  })

  it('emits open on a double-click of the main area', async () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false, current: false } })
    await wrapper.find('.project-row-main').trigger('dblclick')
    expect(wrapper.emitted('open')?.[0]).toEqual([project()])
  })

  it('emits open from the explicit 열기 button', async () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false, current: false } })
    await wrapper.find('.project-row-open').trigger('click')
    expect(wrapper.emitted('open')?.[0]).toEqual([project()])
  })

  it('never nests a button inside a button — the selectable area and 열기 are siblings', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false, current: false } })
    const buttons = wrapper.findAll('button')
    expect(buttons).toHaveLength(2)
    for (const button of buttons) {
      expect(button.find('button').exists()).toBe(false)
    }
  })

  it('the 열기 action reuses the shared Button primitive (ui-btn), not a hand-rolled button', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false, current: false } })
    expect(wrapper.find('.project-row-open').classes()).toContain('ui-btn')
  })

  it('both row actions are native buttons, so Tab/Enter/Space work without custom key handling', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false, current: false } })
    expect(wrapper.find('.project-row-main').element.tagName).toBe('BUTTON')
    expect(wrapper.find('.project-row-open').element.tagName).toBe('BUTTON')
  })

  it('omits the updated line entirely when updatedAt is null', () => {
    const wrapper = mount(ProjectRow, { props: { project: project({ updatedAt: null }), selected: false, current: false } })
    expect(wrapper.find('.project-row-updated').exists()).toBe(false)
  })

  it('renders the Project identification color (D0004 §2.3)', () => {
    const wrapper = mount(ProjectRow, { props: { project: project({ color: '#db2777' }), selected: false, current: false } })
    expect(wrapper.find('.project-row-color').attributes('style')).toContain('background: rgb(219, 39, 119)')
  })

  it('falls back to a neutral color when the Project has no color set', () => {
    const wrapper = mount(ProjectRow, { props: { project: project({ color: null }), selected: false, current: false } })
    const style = wrapper.find('.project-row-color').attributes('style') ?? ''
    expect(style).not.toBe('')
    expect(style).not.toContain('background: null')
    expect(style).not.toContain('background: ;')
  })

  it('reflects a refreshed updatedAt on the same (keyed, reused) row instance, not the value read at first mount', async () => {
    const wrapper = mount(ProjectRow, { props: { project: project({ updatedAt: '2026-09-20T00:00:00+09:00' }), selected: false, current: false } })
    expect(wrapper.find('.project-row-updated').text()).toContain('2026-09-20')

    await wrapper.setProps({ project: project({ updatedAt: '2026-09-26T00:00:00+09:00' }) })
    expect(wrapper.find('.project-row-updated').text()).toContain('2026-09-26')
    expect(wrapper.find('.project-row-updated').text()).not.toContain('2026-09-20')
  })

  it('shows the updated line (not a label with an empty value) once a refresh moves updatedAt from null to a date', async () => {
    const wrapper = mount(ProjectRow, { props: { project: project({ updatedAt: null }), selected: false, current: false } })
    expect(wrapper.find('.project-row-updated').exists()).toBe(false)

    await wrapper.setProps({ project: project({ updatedAt: '2026-09-26T00:00:00+09:00' }) })
    expect(wrapper.find('.project-row-updated').exists()).toBe(true)
    expect(wrapper.find('.project-row-updated').text()).toContain('2026-09-26')
  })
})
