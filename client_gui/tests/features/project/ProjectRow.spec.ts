import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ProjectRow from '@/features/project/ProjectRow.vue'
import type { Project } from '@/features/project/types'
import { setTestLocale } from './projectI18n'

// WP0005 T#2 §2.3/§8: selected is a non-color-only signal, and the row never nests one
// interactive element inside another. flowgate.v02.0040: the row no longer marks the
// current Project at all.
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
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false } })
    expect(wrapper.find('.project-row-name').text()).toBe('Demo Project')
    expect(wrapper.find('.project-row-desc').text()).toBe('A demo')
    const updated = wrapper.find('.project-row-updated').text()
    expect(updated).toContain('최종 수정')
    expect(updated).toContain('2026-09-20')
    expect(updated).not.toContain('최근 사용')
    expect(updated).not.toContain('최근 열기')
  })

  it('never draws a "현재" Badge or aria-current (flowgate.v02.0040)', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false } })
    expect(wrapper.find('.ui-badge').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('현재')
    expect(wrapper.find('.project-row-main').attributes('aria-current')).toBeUndefined()
    expect(wrapper.classes()).not.toContain('is-current')
  })

  it('shows a visible checkmark plus aria-pressed on the focusable main control for selected, not color alone', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: true } })
    expect(wrapper.find('.project-row-main').attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('.project-row-select-mark').text()).toBe('✓')
  })

  it('omits the checkmark and is-selected when not selected', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false } })
    expect(wrapper.find('.project-row-main').attributes('aria-pressed')).toBe('false')
    expect(wrapper.find('.project-row-select-mark').text()).toBe('')
    expect(wrapper.classes()).not.toContain('is-selected')
    expect(wrapper.find('.ui-badge').exists()).toBe(false)
  })

  it('is a plain list item, not an ARIA option widget nesting two interactive descendants', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false } })
    expect(wrapper.attributes('role')).toBeUndefined()
  })

  it('emits select on a single click of the main area, not open', async () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false } })
    await wrapper.find('.project-row-main').trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual([project()])
    expect(wrapper.emitted('open')).toBeUndefined()
  })

  it('emits open on a double-click of the main area', async () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false } })
    await wrapper.find('.project-row-main').trigger('dblclick')
    expect(wrapper.emitted('open')?.[0]).toEqual([project()])
  })

  it('emits open from the explicit 열기 button', async () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false } })
    await wrapper.find('.project-row-open').trigger('click')
    expect(wrapper.emitted('open')?.[0]).toEqual([project()])
  })

  it('never nests a button inside a button — the selectable area and 열기 are siblings', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false } })
    const buttons = wrapper.findAll('button')
    expect(buttons).toHaveLength(2)
    for (const button of buttons) {
      expect(button.find('button').exists()).toBe(false)
    }
  })

  it('the 열기 action reuses the shared Button primitive (ui-btn), not a hand-rolled button', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false } })
    expect(wrapper.find('.project-row-open').classes()).toContain('ui-btn')
  })

  it('both row actions are native buttons, so Tab/Enter/Space work without custom key handling', () => {
    const wrapper = mount(ProjectRow, { props: { project: project(), selected: false } })
    expect(wrapper.find('.project-row-main').element.tagName).toBe('BUTTON')
    expect(wrapper.find('.project-row-open').element.tagName).toBe('BUTTON')
  })

  it('omits the updated line entirely when updatedAt is null', () => {
    const wrapper = mount(ProjectRow, { props: { project: project({ updatedAt: null }), selected: false } })
    expect(wrapper.find('.project-row-updated').exists()).toBe(false)
  })

  it('renders the Project identification color (D0004 §2.3)', () => {
    const wrapper = mount(ProjectRow, { props: { project: project({ color: '#db2777' }), selected: false } })
    expect(wrapper.find('.project-row-color').attributes('style')).toContain('background: rgb(219, 39, 119)')
  })

  it('falls back to a neutral color when the Project has no color set', () => {
    const wrapper = mount(ProjectRow, { props: { project: project({ color: null }), selected: false } })
    const style = wrapper.find('.project-row-color').attributes('style') ?? ''
    expect(style).not.toBe('')
    expect(style).not.toContain('background: null')
    expect(style).not.toContain('background: ;')
  })

  it('reflects a refreshed updatedAt on the same (keyed, reused) row instance, not the value read at first mount', async () => {
    const wrapper = mount(ProjectRow, { props: { project: project({ updatedAt: '2026-09-20T00:00:00+09:00' }), selected: false } })
    expect(wrapper.find('.project-row-updated').text()).toContain('2026-09-20')

    await wrapper.setProps({ project: project({ updatedAt: '2026-09-26T00:00:00+09:00' }) })
    expect(wrapper.find('.project-row-updated').text()).toContain('2026-09-26')
    expect(wrapper.find('.project-row-updated').text()).not.toContain('2026-09-20')
  })

  it('shows the updated line (not a label with an empty value) once a refresh moves updatedAt from null to a date', async () => {
    const wrapper = mount(ProjectRow, { props: { project: project({ updatedAt: null }), selected: false } })
    expect(wrapper.find('.project-row-updated').exists()).toBe(false)

    await wrapper.setProps({ project: project({ updatedAt: '2026-09-26T00:00:00+09:00' }) })
    expect(wrapper.find('.project-row-updated').exists()).toBe(true)
    expect(wrapper.find('.project-row-updated').text()).toContain('2026-09-26')
  })

  // flowgate.v02.0088 F04: the row's words follow the language; the Project name and the date are data.
  it.each([
    ['ja', '最終更新 2026-09-20', '開く'],
    ['en', 'Last modified 2026-09-20', 'Open'],
  ] as const)('labels the date and the open action in %s, leaving name and date as they are', async (locale, updated, open) => {
    const wrapper = mount(ProjectRow, { props: { project: project({ name: '데모 프로젝트' }), selected: false } })
    setTestLocale(locale)
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.project-row-updated').text()).toBe(updated)
    expect(wrapper.find('.project-row-open').text()).toBe(open)
    expect(wrapper.find('.project-row-name').text()).toBe('데모 프로젝트')
  })
})
