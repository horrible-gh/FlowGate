import { mount } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeAll, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import Sidebar from '@/app/components/Sidebar.vue'
import Topbar from '@/app/components/Topbar.vue'
import DashboardSidePanel from '@/app/dashboard/DashboardSidePanel.vue'
import DashboardTip from '@/app/dashboard/DashboardTip.vue'
import { registerAppMessages } from '@/app/i18n'

registerAppMessages(i18n)

// T0004 §8 / rejection 2026-09-25: swapping a native element for a UI Primitive must not
// change the screen's already-shipped computed style. Each Primitive applies its own
// scoped disabled-opacity / font-weight / fixed-size rule unconditionally (Button
// `font-weight: 650`, Button/IconButton `:disabled { opacity: --fg2-control-disabled-*
// }`, Input `:disabled { opacity: --fg2-control-disabled-opacity }`, IconButton `--sm
// { width/height: 28px }`), so a caller that must keep HEAD's pre-Primitive look has to
// win that cascade with an explicit override, not just reuse the same class name — jsdom
// resolves real CSS specificity/cascade for non-layout properties (opacity, font-weight),
// so this loads the actual scoped `<style>` bodies (no build step, no data-v hash to
// chase — see the "0416 TR0005" recipe) and reads getComputedStyle on the real mounted
// DOM instead of asserting against a source-text pattern.
function scopedStyleBody(relPathFromSrc: string): string {
  const text = readFileSync(resolve(__dirname, '../../src', relPathFromSrc), 'utf8')
  const match = text.match(/<style scoped>([\s\S]*?)<\/style>/)
  if (!match) throw new Error(`no <style scoped> block in ${relPathFromSrc}`)
  // :deep(x) has no meaning once scoped attribute selectors are removed; plain `x` is
  // equivalent for cascade purposes here since nothing else in this probe scopes it.
  return match[1].replace(/:deep\(([^)]*)\)/g, '$1')
}

beforeAll(() => {
  const css = [
    readFileSync(resolve(__dirname, '../../src/app/styles/theme.css'), 'utf8'),
    scopedStyleBody('components/ui/IconButton.vue'),
    scopedStyleBody('components/ui/Button.vue'),
    scopedStyleBody('components/ui/Badge.vue'),
    scopedStyleBody('components/ui/Input.vue'),
    scopedStyleBody('app/components/Topbar.vue'),
    scopedStyleBody('app/components/Sidebar.vue'),
    scopedStyleBody('app/dashboard/DashboardSidePanel.vue'),
    scopedStyleBody('app/dashboard/DashboardTip.vue'),
  ].join('\n')
  const style = document.createElement('style')
  style.textContent = css
  document.head.appendChild(style)
})

function mountWithRouter<T>(component: T) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div/>' } }, { path: '/:rest(.*)*', component: { template: '<div/>' } }],
  })
  return mount(component as never, { global: { plugins: [i18n, router] } })
}

describe('App Shell / Dashboard: Primitive swap keeps HEAD baseline computed style', () => {
  it('Topbar search input stays fully opaque while disabled (HEAD never dimmed it)', () => {
    const wrapper = mountWithRouter(Topbar)
    const input = wrapper.get('.gui-search-input').element as HTMLElement
    expect(getComputedStyle(input).opacity).toBe('1')
  })

  it('Topbar bell stays 24x24 and fully opaque while disabled (HEAD never sized it 28x28 or dimmed it)', () => {
    const wrapper = mountWithRouter(Topbar)
    const bell = wrapper.get('.gui-bell').element as HTMLElement
    const cs = getComputedStyle(bell)
    expect(cs.width).toBe('24px')
    expect(cs.height).toBe('24px')
    expect(cs.opacity).toBe('1')
  })

  it('Sidebar help control stays regular weight and fully opaque while disabled (HEAD never bolded or dimmed it)', () => {
    const wrapper = mountWithRouter(Sidebar)
    const help = wrapper.get('.gui-help').element as HTMLElement
    const cs = getComputedStyle(help)
    expect(cs.fontWeight).toBe('normal')
    expect(cs.opacity).toBe('1')
  })

  it('Dashboard side panel status badge stays regular weight (HEAD never bolded it)', () => {
    const wrapper = mount(DashboardSidePanel, { props: { panel: 'recent_projects' as never }, global: { plugins: [i18n] } })
    const status = wrapper.get('.dash-panel-status').element as HTMLElement
    expect(getComputedStyle(status).fontWeight).toBe('normal')
  })

  it('Dashboard tip close control is not forced into a fixed 28x28 box (HEAD sized it from its own content)', () => {
    const wrapper = mount(DashboardTip, { global: { plugins: [i18n] } })
    const close = wrapper.get('.dash-tip-close').element as HTMLElement
    const cs = getComputedStyle(close)
    expect(cs.width).toBe('auto')
    expect(cs.height).toBe('auto')
  })
})
