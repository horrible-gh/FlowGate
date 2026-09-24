import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import App from '@/app/App.vue'
import { registerAppMessages } from '@/app/i18n'

const APP_VUE_SRC = readFileSync(resolve(__dirname, '../../src/app/App.vue'), 'utf8')

registerAppMessages(i18n)

// T0009 §8: App.vue (the App Shell) owns the Topbar, not any per-view component, so
// every /app/* route rendered through RouterView gets the switch for free. This mounts
// the real App.vue + router combination (like tests/app/router.deepLink.spec.ts) rather
// than Topbar in isolation, to prove the shell actually wraps whatever route matches.
async function loadRouterFresh() {
  vi.resetModules()
  const mod = await import('@/app/router/index')
  return mod.default
}

describe('client_gui App Shell renders a persistent Topbar (T0009 §8)', () => {
  beforeEach(() => {
    sessionStorage.clear()
    delete window.__accessToken__
    window.history.replaceState({}, '', '/app/')
    i18n.global.locale.value = 'ko'
  })

  it('renders the Topbar switch button alongside the matched route, on a deep /app/* path too', async () => {
    window.__accessToken__ = 'token-shell-test'
    window.history.replaceState({}, '', '/app/projects')
    const router = await loadRouterFresh()
    await router.push('/projects')

    const wrapper = mount(App, {
      global: { plugins: [i18n, router] },
    })
    await router.isReady()

    expect(wrapper.find('.app-header .gui-switch-btn').exists()).toBe(true)
    // The route content (T0011: the /app/projects "준비 중" placeholder) still renders
    // beneath/alongside the shell, inside the shell's content area.
    expect(wrapper.find('.gui-route-content .gui-coming-soon').exists()).toBe(true)

    wrapper.unmount()
  })

  // Rejection (rev0): App.vue reused the shared `.app-shell` class from
  // client/shared/app.css, whose grid is `grid-template-columns: var(--sdb-w) 1fr` for
  // v0.1's sidebar layout. Only `.app-header` (the Topbar) carries `grid-column: 1/-1`
  // there, so `RouterView`'s route root had no grid-column of its own and CSS grid
  // auto-placed it into the 280px sidebar cell instead of the full width. jsdom doesn't
  // run real grid layout, so this asserts the source contract directly: the shell root
  // must not opt into the shared 2-column class, and its own grid must declare a single
  // content column (T0011 puts the sidebar/content split in the body's own grid instead).
  it('does not confine the routed view to the shared sidebar column (T0009 §10/§12)', () => {
    expect(rootShellClass(APP_VUE_SRC)).not.toBe('app-shell')

    const styleBlock = APP_VUE_SRC.match(/<style scoped>([\s\S]*?)<\/style>/)?.[1] ?? ''
    const gridColumns = styleBlock.match(/grid-template-columns:\s*([^;]+);/)?.[1]?.trim()
    expect(gridColumns).toBeTruthy()
    // A single track (e.g. `minmax(0, 1fr)` or `1fr`) — not the shared `var(--sdb-w) 1fr`
    // two-column definition that traps route content at sidebar width. Collapse
    // function-call parens first so `minmax(0, 1fr)`'s internal comma/space isn't
    // mistaken for a second track.
    expect(gridColumns).not.toContain('--sdb-w')
    const trackCount = gridColumns!.replace(/\([^)]*\)/g, 'X').trim().split(/\s+/).length
    expect(trackCount).toBe(1)
  })
})

function rootShellClass(src: string): string | undefined {
  return src.match(/<template>\s*<div class="([^"]+)">/)?.[1]
}
