import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ProjectList from '@/features/project/ProjectList.vue'
import type { Project } from '@/features/project/types'
import { setTestLocale } from './projectI18n'

// WP0005 T#2 §2.1/§3.1/§4: loading/error/empty(none)/empty(search) are distinct, and
// selecting vs opening a row is delegated straight through to the caller.
function project(id: string): Project {
  return { id, name: id, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

function baseProps() {
  return {
    projects: [] as Project[],
    loading: false,
    error: null as string | null,
    hasAnyProjects: false,
    selectedId: null as string | null,
  }
}

describe('features/project/ProjectList', () => {
  it('shows LoadingState while loading, before checking anything else', () => {
    const wrapper = mount(ProjectList, { props: { ...baseProps(), loading: true, error: 'ignored while loading' } })
    expect(wrapper.find('.ui-loading-state').exists()).toBe(true)
    expect(wrapper.find('.ui-error-state').exists()).toBe(false)
  })

  it('shows ErrorState with a retry action on error', async () => {
    const wrapper = mount(ProjectList, { props: { ...baseProps(), error: 'offline' } })
    expect(wrapper.find('.ui-error-state').exists()).toBe(true)
    expect(wrapper.text()).toContain('offline')
    await wrapper.find('.ui-error-state button').trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
  })

  it('shows a "no projects at all" EmptyState distinct from a "no search results" one', () => {
    const none = mount(ProjectList, { props: { ...baseProps(), hasAnyProjects: false } })
    expect(none.find('.ui-empty-state-title').text()).toBe('프로젝트가 없습니다.')

    const noResults = mount(ProjectList, { props: { ...baseProps(), hasAnyProjects: true } })
    expect(noResults.find('.ui-empty-state-title').text()).toBe('일치하는 프로젝트가 없습니다.')
    expect(noResults.find('.ui-empty-state-title').text()).not.toBe(none.find('.ui-empty-state-title').text())
  })

  it('renders one ProjectRow per project as a plain list (no listbox/option ARIA widget), forwarding selected by id and no current mark', () => {
    const wrapper = mount(ProjectList, {
      props: { ...baseProps(), projects: [project('a'), project('b')], hasAnyProjects: true, selectedId: 'b' },
    })
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
    expect(wrapper.find('[role="option"]').exists()).toBe(false)
    const mains = wrapper.findAll('.project-row-main')
    expect(mains).toHaveLength(2)
    expect(mains[0].attributes('aria-current')).toBeUndefined()
    expect(mains[0].attributes('aria-pressed')).toBe('false')
    expect(mains[1].attributes('aria-current')).toBeUndefined()
    expect(mains[1].attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('.ui-badge').exists()).toBe(false)
  })

  it('forwards select/open events from a row up to its own listeners', async () => {
    const wrapper = mount(ProjectList, {
      props: { ...baseProps(), projects: [project('a')], hasAnyProjects: true },
    })
    await wrapper.find('.project-row-main').trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual([project('a')])
    await wrapper.find('.project-row-open').trigger('click')
    expect(wrapper.emitted('open')?.[0]).toEqual([project('a')])
  })

  // flowgate.v02.0088 F03/F10: the list's own words follow the language; the server's diagnostic stays as sent and the
  // localized title stands alone when there is none.
  it.each([
    ['ja', 'プロジェクトを読み込めませんでした。', '再試行', 'プロジェクトがありません。', 'プロジェクト一覧'],
    ['en', 'Could not load projects.', 'Retry', 'No projects yet.', 'Project list'],
  ] as const)('shows its states in %s, keeping the server words and project data as they came', async (locale, failed, retry, none, aria) => {
    setTestLocale(locale)
    const error = mount(ProjectList, { props: { ...baseProps(), error: '서버 점검 중' } })
    expect(error.find('.ui-error-state-title').text()).toBe(failed)
    expect(error.find('.ui-error-state-message').text()).toBe('서버 점검 중')
    expect(error.find('.ui-error-state button').text()).toBe(retry)
    const silent = mount(ProjectList, { props: { ...baseProps(), error: '' } })
    expect(silent.find('.ui-error-state-title').text()).toBe(failed)
    expect(silent.find('.ui-error-state-message').exists()).toBe(false)
    expect(mount(ProjectList, { props: { ...baseProps(), hasAnyProjects: false } }).find('.ui-empty-state-title').text()).toBe(none)
    const list = mount(ProjectList, { props: { ...baseProps(), projects: [project('프로젝트-가')], hasAnyProjects: true } })
    expect(list.find('ul').attributes('aria-label')).toBe(aria)
    expect(list.find('.project-row-name').text()).toBe('프로젝트-가')
  })

  it('changes its words on a language switch while it stays mounted', async () => {
    const wrapper = mount(ProjectList, { props: { ...baseProps(), error: '' } })
    expect(wrapper.find('.ui-error-state-title').text()).toBe('프로젝트를 불러오지 못했습니다.')
    setTestLocale('ja')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.ui-error-state-title').text()).toBe('プロジェクトを読み込めませんでした。')
  })
})
