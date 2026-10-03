import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Router } from 'vue-router'
import i18n from '@shared/i18n'
import App from '@/app/App.vue'
import { registerAppMessages } from '@/app/i18n'

registerAppMessages(i18n)

// flowgate.v02.0025 (0003-D §3.1/§7.1): the existing Designer entry points — Sidebar menu,
// Dashboard card, direct URL / refresh — now open the Designer Shell through the real
// App.vue + real /app router, while the other placeholder screens stay "준비 중".
// T0006 §1: the Designer is a full-screen workspace — App.vue renders no Topbar / Sidebar
// on any /designer path, and every other screen keeps both.
async function mountAt(url: string): Promise<{ wrapper: VueWrapper; router: Router }> {
  window.history.replaceState({}, '', `/app${url}`)
  vi.resetModules()
  const router = (await import('@/app/router/index')).default
  await router.push(url)
  const wrapper = mount(App, { global: { plugins: [i18n, router, createPinia()] } })
  await router.isReady()
  return { wrapper, router }
}

const settle = () => new Promise((r) => setTimeout(r, 0))

const hasTopbar = (wrapper: VueWrapper) => wrapper.find('header.gui-topbar').exists()
const hasSidebar = (wrapper: VueWrapper) => wrapper.find('aside.gui-sidebar').exists()

function expectShell(wrapper: VueWrapper) {
  expect(wrapper.find('.gui-route-content [data-test="designer-shell"]').exists()).toBe(true)
  expect(wrapper.find('.gui-coming-soon').exists()).toBe(false)
  // Full-screen workspace: no App chrome around the Shell (T0006 §1).
  expect(hasTopbar(wrapper)).toBe(false)
  expect(hasSidebar(wrapper)).toBe(false)
  expect(wrapper.get('.gui-app-shell').classes()).toContain('is-fullscreen')
}

function expectChrome(wrapper: VueWrapper) {
  expect(hasTopbar(wrapper)).toBe(true)
  expect(hasSidebar(wrapper)).toBe(true)
  expect(wrapper.get('.gui-app-shell').classes()).not.toContain('is-fullscreen')
}

let mounted: VueWrapper | null = null

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  window.__accessToken__ = 'token-designer-test'
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  mounted?.unmount()
  mounted = null
  delete window.__accessToken__
})

describe('Designer entry (flowgate.v02.0025)', () => {
  it('Sidebar [디자이너] opens /app/designer and renders the Designer Shell', async () => {
    const { wrapper, router } = await mountAt('/')
    mounted = wrapper

    await wrapper.get('[data-nav-key="designer"]').trigger('click')
    await router.isReady()
    await settle()

    expect(router.currentRoute.value.fullPath).toBe('/designer')
    expect(router.currentRoute.value.name).toBe('designer')
    expect(window.location.pathname).toBe('/app/designer')
    expectShell(wrapper)
    expect(wrapper.find('.dash').exists()).toBe(false)
  })

  it('the Dashboard Designer card opens the Designer Shell', async () => {
    const { wrapper, router } = await mountAt('/')
    mounted = wrapper

    await wrapper.get('[data-card-key="designer"]').trigger('click')
    await settle()

    expect(router.currentRoute.value.fullPath).toBe('/designer')
    expectShell(wrapper)
  })

  it('direct access / refresh of /designer renders the Shell and keeps the URL', async () => {
    const { wrapper, router } = await mountAt('/designer')
    mounted = wrapper

    expect(router.currentRoute.value.fullPath).toBe('/designer')
    expect(window.location.pathname).toBe('/app/designer')
    expectShell(wrapper)
  })

  it('a deep /designer path keeps its URL (no redirect) and renders the same Shell', async () => {
    const { wrapper, router } = await mountAt('/designer/x/y?tab=z')
    mounted = wrapper

    expect(router.currentRoute.value.fullPath).toBe('/designer/x/y?tab=z')
    expect(router.currentRoute.value.name).toBe('designer')
    expect(window.location.pathname + window.location.search).toBe('/app/designer/x/y?tab=z')
    expectShell(wrapper)
  })

  it.each([
    ['/projects', 'projects'],
    ['/git', 'git'],
    ['/settings', 'settings'],
  ])('%s is still the "준비 중" placeholder', async (url, key) => {
    const { wrapper } = await mountAt(url)
    mounted = wrapper

    expect(wrapper.get('.gui-coming-soon').attributes('data-nav-key')).toBe(key)
    expect(wrapper.find('[data-test="designer-shell"]').exists()).toBe(false)
    expectChrome(wrapper)
  })

  it('/ai and unknown paths are unchanged', async () => {
    const ai = await mountAt('/ai')
    mounted = ai.wrapper
    expect(ai.wrapper.find('.ai-settings-view').exists()).toBe(true)
    expectChrome(ai.wrapper)
    ai.wrapper.unmount()

    const missing = await mountAt('/designerx')
    mounted = missing.wrapper
    expect(missing.router.currentRoute.value.name).toBe('not-found')
    expect(missing.wrapper.find('[data-test="designer-shell"]').exists()).toBe(false)
    expectChrome(missing.wrapper)
  })
})

describe('Designer full-screen workspace (flowgate.v02.0025 T0006 §1)', () => {
  it('the Dashboard keeps its Topbar and Sidebar', async () => {
    const { wrapper } = await mountAt('/')
    mounted = wrapper
    expect(wrapper.find('.dash').exists()).toBe(true)
    expectChrome(wrapper)
  })

  it('leaving the Designer through the router brings the Topbar and Sidebar back', async () => {
    const { wrapper, router } = await mountAt('/designer')
    mounted = wrapper
    expectShell(wrapper)

    await router.push('/')
    await settle()
    expect(wrapper.find('[data-test="designer-shell"]').exists()).toBe(false)
    expectChrome(wrapper)
    expect(wrapper.get('[data-nav-key="dashboard"]').classes()).toContain('active')

    await router.push('/designer/x')
    await settle()
    expectShell(wrapper)
  })

  it('only the designer route is marked full-screen', async () => {
    vi.resetModules()
    const router = (await import('@/app/router/index')).default
    const fullscreen = router.getRoutes().filter((r) => r.meta.fullscreen === true).map((r) => r.name)
    expect(fullscreen).toEqual(['designer'])
  })
})

describe('Designer entry keeps the auth guard contract (flowgate.v02.0025)', () => {
  it('a logged-out /designer goes to /login with next=/app/designer before the Shell renders', async () => {
    delete window.__accessToken__
    window.history.replaceState({}, '', '/app/designer')
    vi.resetModules()
    const router = (await import('@/app/router/index')).default
    const originalLocation = window.location
    delete (window as unknown as { location?: unknown }).location
    ;(window as unknown as { location: { pathname: string; search: string; href: string } }).location = {
      pathname: '/app/designer',
      search: '',
      href: '',
    }
    try {
      const failure = await router.push('/designer')
      expect(failure).toBeTruthy()
      expect(window.location.href).toBe(`/login?next=${encodeURIComponent('/app/designer')}`)
    } finally {
      ;(window as unknown as { location: Location }).location = originalLocation
    }
  })
})
