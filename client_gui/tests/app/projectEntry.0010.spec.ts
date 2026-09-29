import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Router } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import type { Project } from '@/features/project/types'

const fetchActiveProjects = vi.hoisted(() => vi.fn())
const createProjectApi = vi.hoisted(() => vi.fn())
const suggestProjectSlug = vi.hoisted(() => vi.fn())

vi.mock('@/features/project/api', () => ({
  fetchActiveProjects,
  createProject: createProjectApi,
  suggestProjectSlug,
}))

registerAppMessages(i18n)

function project(id: string, overrides: Partial<Project> = {}): Project {
  return {
    id,
    name: id,
    description: `desc-${id}`,
    color: null,
    isActive: true,
    createdAt: null,
    updatedAt: null,
    modules: [],
    ...overrides,
  }
}

function flushPromises(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

// ModalShell content lives behind a Teleport(to="body") -- same convention as
// tests/features/project/ProjectDialog.spec.ts -- so it is out of `wrapper`'s own tree and
// interactions go through `document` with a dispatched, bubbling native event.
function clickOn(selector: string): void {
  const el = document.querySelector(selector)
  if (!el) throw new Error(`clickOn: no element for ${selector}`)
  el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

async function mountAppAt(url: string): Promise<{ wrapper: VueWrapper; router: Router }> {
  window.history.replaceState({}, '', `/app${url}`)
  vi.resetModules()
  const [{ default: router }, { default: App }] = await Promise.all([
    import('@/app/router/index'),
    import('@/app/App.vue'),
  ])
  await router.push(url)
  const wrapper = mount(App, { global: { plugins: [i18n, router, createPinia()] }, attachTo: document.body })
  await router.isReady()
  return { wrapper, router }
}

let mounted: VueWrapper | null = null

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  window.__accessToken__ = 'token-project-entry-test'
  i18n.global.locale.value = 'ko'
  fetchActiveProjects.mockReset().mockResolvedValue([project('alpha'), project('beta')])
  createProjectApi.mockReset()
  suggestProjectSlug.mockReset().mockResolvedValue(null)
})

afterEach(() => {
  mounted?.unmount()
  mounted = null
  document.body.querySelectorAll('.ui-modal-overlay').forEach((el) => el.remove())
  delete window.__accessToken__
})

// T0010 (T#3) §1/§2/§4: Sidebar and Dashboard both open the one Project Dialog App.vue
// mounts, through the feature's public `openDialog()` interface, without ever routing to a
// /projects screen.
describe('T#3 App Shell entry points open the single Project Dialog (T0010 §1/§2/§4)', () => {
  it('Sidebar Project click opens the Dialog with no route change, and the create entry stays hidden for a non-admin', async () => {
    const { wrapper, router } = await mountAppAt('/')
    mounted = wrapper

    await wrapper.get('[data-nav-key="projects"]').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(router.currentRoute.value.fullPath).toBe('/')
    expect(window.location.pathname).toBe('/app/')
    expect(document.querySelector('.ui-modal-overlay')).not.toBeNull()
    expect(document.querySelectorAll('.project-row')).toHaveLength(2)
    expect(document.querySelector('.pd-intro button')).toBeNull()
    // The Dashboard underneath is untouched -- App never navigated away from it.
    expect(wrapper.find('.dash').exists()).toBe(true)
  })

  it('Dashboard Project card click opens the same Dialog with no route change', async () => {
    const { wrapper, router } = await mountAppAt('/')
    mounted = wrapper

    await wrapper.get('[data-card-key="projects"]').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(router.currentRoute.value.fullPath).toBe('/')
    expect(window.location.pathname).toBe('/app/')
    expect(document.querySelector('.ui-modal-overlay')).not.toBeNull()
    expect(document.querySelectorAll('.project-row')).toHaveLength(2)
  })

  it('a modified click on the Sidebar entry does not open the Dialog and leaves the real /projects href for the browser', async () => {
    const { wrapper, router } = await mountAppAt('/')
    mounted = wrapper

    const link = wrapper.get('[data-nav-key="projects"]')
    expect(link.attributes('href')).toBe('/app/projects')
    await link.trigger('click', { ctrlKey: true })
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/')
    expect(document.querySelector('.ui-modal-overlay')).toBeNull()
  })

  it('only one Dialog instance exists no matter which entry point opened it (single mount, shared store)', async () => {
    const { wrapper } = await mountAppAt('/')
    mounted = wrapper

    await wrapper.get('[data-nav-key="projects"]').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()
    expect(document.querySelectorAll('.ui-modal-overlay')).toHaveLength(1)

    // Closing and reopening from the *other* entry point still drives the same overlay --
    // there is exactly one ProjectDialog mounted in the App Shell (T0010 §2).
    clickOn('.pd-head .ui-icon-btn')
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.ui-modal-overlay')).toBeNull()

    await wrapper.get('[data-card-key="projects"]').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()
    expect(document.querySelectorAll('.ui-modal-overlay')).toHaveLength(1)
  })

  it('closing the Dialog returns to the same screen underneath (no navigation ever happened)', async () => {
    const { wrapper, router } = await mountAppAt('/git')
    mounted = wrapper

    await wrapper.get('[data-nav-key="projects"]').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()
    expect(document.querySelector('.ui-modal-overlay')).not.toBeNull()

    clickOn('.pd-head .ui-icon-btn')
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.ui-modal-overlay')).toBeNull()
    expect(router.currentRoute.value.fullPath).toBe('/git')
    expect(wrapper.get('.gui-coming-soon h1').text()).toBe('Git')
    expect(wrapper.get('[data-nav-key="git"]').classes()).toContain('active')
  })

  it('opening a Project, closing, and reopening the Dialog shows that Project as current -- and never touches Git branch state', async () => {
    const { wrapper } = await mountAppAt('/')
    mounted = wrapper

    await wrapper.get('[data-nav-key="projects"]').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()

    clickOn('[data-project-id="beta"] .project-row-open')
    await wrapper.vm.$nextTick()

    // "열기" is current-confirm + close only (T0010 §3): no Designer route, no branch reset.
    expect(document.querySelector('.ui-modal-overlay')).toBeNull()
    expect(window.location.pathname).toBe('/app/')
    expect(localStorage.getItem('fg_current_branch')).toBeNull()
    expect(localStorage.getItem('fg_current_project_id')).toBe('beta')

    await wrapper.get('[data-nav-key="projects"]').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()

    const betaRow = document.querySelector('[data-project-id="beta"] .project-row-main')
    expect(betaRow?.getAttribute('aria-current')).toBe('true')
    expect(betaRow?.textContent).toContain('현재')
    const alphaRow = document.querySelector('[data-project-id="alpha"] .project-row-main')
    expect(alphaRow?.getAttribute('aria-current')).toBeNull()
  })

  it('mounts and opens without error when the stored current project id is invalid and the active list is empty', async () => {
    localStorage.setItem('fg_current_project_id', 'does-not-exist')
    fetchActiveProjects.mockResolvedValue([])
    const { wrapper } = await mountAppAt('/')
    mounted = wrapper

    await expect(wrapper.get('[data-nav-key="projects"]').trigger('click')).resolves.toBeUndefined()
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(document.querySelector('.ui-modal-overlay')).not.toBeNull()
    expect(document.querySelector('.ui-empty-state-title')?.textContent).toBe('프로젝트가 없습니다.')
  })
})
