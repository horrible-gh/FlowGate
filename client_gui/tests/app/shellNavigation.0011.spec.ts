import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Router } from 'vue-router'
import i18n from '@shared/i18n'
import App from '@/app/App.vue'
import { registerAppMessages } from '@/app/i18n'

registerAppMessages(i18n)

// T0011 §8/§12: the real App.vue + real /app router. Sidebar active state must follow the
// router, placeholders must say "준비 중", and no navigation may rewrite the requested URL.
// App.vue mounts the Project Dialog (T0010/T#3 §2), so a real Pinia instance is required
// the same way main.ts installs one — this is not the Project feature's own test surface,
// just what App.vue now needs to mount at all.
async function mountAt(url: string): Promise<{ wrapper: VueWrapper; router: Router }> {
  window.history.replaceState({}, '', `/app${url}`)
  vi.resetModules()
  const router = (await import('@/app/router/index')).default
  await router.push(url)
  const wrapper = mount(App, { global: { plugins: [i18n, router, createPinia()] } })
  await router.isReady()
  return { wrapper, router }
}

function activeKeys(wrapper: VueWrapper): string[] {
  return wrapper.findAll('.gui-nav-item.active').map((a) => a.attributes('data-nav-key') ?? '')
}

let mounted: VueWrapper | null = null

beforeEach(() => {
  sessionStorage.clear()
  window.__accessToken__ = 'token-nav-test'
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  mounted?.unmount()
  mounted = null
  delete window.__accessToken__
})

describe('v0.2 Sidebar navigation (T0011 §7/§12)', () => {
  it('lists the six mockup screens in order with their /app destinations', async () => {
    const { wrapper } = await mountAt('/')
    mounted = wrapper

    const items = wrapper.findAll('.gui-sidebar-nav a.gui-nav-item')
    expect(items.map((a) => a.find('.gui-nav-title').text())).toEqual([
      '대시보드',
      '프로젝트',
      '디자이너',
      'Git',
      'AI 설정',
      '환경 설정',
    ])
    expect(items.map((a) => a.find('.gui-nav-sub').text())).toEqual([
      '홈으로 돌아가기',
      '프로젝트 관리',
      '그룹 · 흐름 설계',
      '저장소 관리',
      'AI 그룹 및 정책',
      '워크스페이스 설정',
    ])
    expect(items.map((a) => a.attributes('href'))).toEqual([
      '/app/',
      '/app/projects',
      '/app/designer',
      '/app/git',
      '/app/ai',
      '/app/settings',
    ])
    expect(wrapper.get('.gui-sidebar-nav').attributes('aria-label')).toBe('주요 화면')
  })

  it('marks Dashboard active (class + aria-current) on /app and renders the Dashboard', async () => {
    const { wrapper } = await mountAt('/')
    mounted = wrapper

    expect(activeKeys(wrapper)).toEqual(['dashboard'])
    expect(wrapper.get('[data-nav-key="dashboard"]').attributes('aria-current')).toBe('page')
    expect(wrapper.find('[data-nav-key="projects"]').attributes('aria-current')).toBeUndefined()
    expect(wrapper.find('.gui-route-content .dash').exists()).toBe(true)
  })

  it('moves the active state when a Sidebar link is clicked, without leaving the SPA', async () => {
    const { wrapper, router } = await mountAt('/')
    mounted = wrapper

    await wrapper.get('[data-nav-key="git"]').trigger('click')
    await router.isReady()
    await new Promise((r) => setTimeout(r, 0))

    expect(router.currentRoute.value.fullPath).toBe('/git')
    expect(window.location.pathname).toBe('/app/git')
    expect(activeKeys(wrapper)).toEqual(['git'])
    expect(wrapper.find('.dash').exists()).toBe(false)
    expect(wrapper.get('.gui-coming-soon h1').text()).toBe('Git')
  })

  it.each([
    ['/projects', 'projects', '프로젝트'],
    ['/designer', 'designer', '디자이너'],
    ['/git', 'git', 'Git'],
  ])('direct access to %s renders an explicit "준비 중" placeholder with that section active', async (url, key, title) => {
    const { wrapper, router } = await mountAt(url)
    mounted = wrapper

    expect(router.currentRoute.value.fullPath).toBe(url)
    expect(activeKeys(wrapper)).toEqual([key])
    const placeholder = wrapper.get('.gui-coming-soon')
    expect(placeholder.attributes('data-nav-key')).toBe(key)
    expect(placeholder.get('h1').text()).toBe(title)
    expect(placeholder.get('.gui-coming-soon-badge').text()).toBe('준비 중')
    expect(placeholder.text()).toContain('아직 구현되지 않았습니다')
    // Points at the existing v0.1 screens via the real switch label, and back home.
    expect(placeholder.text()).toContain('[v0.1로 전환]')
    expect(placeholder.get('a.gui-coming-soon-back').attributes('href')).toBe('/app/')
    // A placeholder never embeds a v0.1 screen.
    expect(wrapper.find('iframe').exists()).toBe(false)
  })

  it('direct access to /settings renders the Environment Settings hub with that section active', async () => {
    // flowgate.v02.0020: /settings is no longer a placeholder (시안 11 #hub).
    const { wrapper, router } = await mountAt('/settings')
    mounted = wrapper

    expect(router.currentRoute.value.fullPath).toBe('/settings')
    expect(activeKeys(wrapper)).toEqual(['settings'])
    expect(wrapper.find('.settings-hub').exists()).toBe(true)
    expect(wrapper.find('.gui-coming-soon').exists()).toBe(false)
  })

  it('direct access to /ai renders the real AI Settings view with AI section active', async () => {
    const { wrapper, router } = await mountAt('/ai')
    mounted = wrapper

    expect(router.currentRoute.value.fullPath).toBe('/ai')
    expect(activeKeys(wrapper)).toEqual(['ai'])
    expect(wrapper.find('.ai-settings-view').exists()).toBe(true)
  })

  it('keeps a deep path under a section (refresh-style) and its section highlighted', async () => {
    const { wrapper, router } = await mountAt('/projects/abc/def?tab=x')
    mounted = wrapper

    expect(router.currentRoute.value.fullPath).toBe('/projects/abc/def?tab=x')
    expect(window.location.pathname + window.location.search).toBe('/app/projects/abc/def?tab=x')
    expect(activeKeys(wrapper)).toEqual(['projects'])
  })

  it('renders an in-place not-found screen for an unknown /app path, with no section active', async () => {
    const { wrapper, router } = await mountAt('/projectsx')
    mounted = wrapper

    expect(router.currentRoute.value.name).toBe('not-found')
    expect(window.location.pathname).toBe('/app/projectsx')
    expect(activeKeys(wrapper)).toEqual([])
    expect(wrapper.get('.gui-not-found h1').text()).toBe('화면을 찾을 수 없습니다')
    expect(wrapper.get('.gui-not-found').text()).toContain('/app/projectsx')
  })

  it('shows the help entry as a disabled control and the version block', async () => {
    const { wrapper } = await mountAt('/')
    mounted = wrapper

    const help = wrapper.get('.gui-sidebar-bottom button.gui-help')
    expect(help.attributes('disabled')).toBeDefined()
    expect(help.text()).toContain('도움말')
    expect(wrapper.get('.gui-version').text()).toBe('FlowGate v0.2Build test-build')
  })
})

describe('auth guard stays the T#1 contract under the new routes (T0011 §12)', () => {
  it('blocks a logged-out placeholder deep link before any screen renders', async () => {
    delete window.__accessToken__
    window.history.replaceState({}, '', '/app/git')
    vi.resetModules()
    const router = (await import('@/app/router/index')).default
    const originalLocation = window.location
    delete (window as unknown as { location?: unknown }).location
    ;(window as unknown as { location: { pathname: string; search: string; href: string } }).location = {
      pathname: '/app/git',
      search: '',
      href: '',
    }
    try {
      const failure = await router.push('/git')
      expect(failure).toBeTruthy()
      expect(window.location.href).toBe(`/login?next=${encodeURIComponent('/app/git')}`)
    } finally {
      ;(window as unknown as { location: Location }).location = originalLocation
    }
  })
})