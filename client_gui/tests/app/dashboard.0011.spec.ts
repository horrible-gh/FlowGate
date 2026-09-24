import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import DashboardView from '@/app/dashboard/DashboardView.vue'
import { registerAppMessages } from '@/app/i18n'
import { blockedRequests } from '../setup/blockNetwork'

registerAppMessages(i18n)

const Stub = { template: '<div />' }

async function mountDashboard() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: DashboardView },
      { path: '/:rest(.*)*', component: Stub },
    ],
  })
  await router.push('/')
  const wrapper = mount(DashboardView, { global: { plugins: [i18n, router] } })
  await router.isReady()
  return { wrapper, router }
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

// T0011 §4/§7/§15: the Dashboard reproduces 01-dashboard.html's hero, six action cards,
// tip and four right-hand panels — linking to screens without faking any feature.
describe('v0.2 Dashboard (T0011)', () => {
  it('renders the mockup hero copy', async () => {
    const { wrapper } = await mountDashboard()

    const h1 = wrapper.get('#dash-hero-title')
    expect(h1.element.tagName).toBe('H1')
    expect(h1.text()).toBe('좋은 하루입니다.지금, 새로운 가능성을 이어가세요.')
    expect(h1.findAll('br')).toHaveLength(1)
    expect(wrapper.get('.dash-hero p').text()).toBe('FlowGate는 아이디어를 실제 작업으로 이어가는 공간입니다.')
    expect(wrapper.get('.dash-quote-text').text()).toBe('“작은 아이디어도\n올바른 흐름을 만나면\n큰 변화를 만듭니다.”')
    expect(wrapper.get('.dash-quote b').text()).toBe('— FlowGate')
  })

  it('renders the six action cards in mockup order with their titles, copy and actions', async () => {
    const { wrapper } = await mountDashboard()

    const cards = wrapper.findAll('.dash-card-grid > .dash-card')
    expect(cards.map((c) => c.attributes('data-card-key'))).toEqual(['projects', 'designer', 'git', 'ai', 'settings', 'quick'])
    expect(cards.map((c) => c.get('.dash-card-title').text().replace('→', '').trim())).toEqual([
      '프로젝트',
      '디자이너',
      'Git',
      'AI 설정',
      '환경 설정',
      '빠른 시작',
    ])
    expect(cards.map((c) => c.get('.dash-card-action').text().replace('›', '').trim())).toEqual([
      '프로젝트 열기',
      '디자이너로 이동',
      'Git 관리하기',
      'AI 설정 열기',
      '설정 열기',
      '준비 중',
    ])
    expect(cards[0].get('.dash-card-desc').text()).toBe('최근 프로젝트를 열거나\n기존 프로젝트를 불러와서\n작업을 이어가세요.')
    // Mockup tones (.card.blue/.cyan/.purple/.blue/.orange/.indigo).
    expect(cards.map((c) => c.classes().find((k) => k.startsWith('tone-')))).toEqual([
      'tone-blue',
      'tone-cyan',
      'tone-purple',
      'tone-blue',
      'tone-orange',
      'tone-indigo',
    ])
  })

  it('makes the five screen cards real links and the "빠른 시작" card an inert, non-link card', async () => {
    const { wrapper } = await mountDashboard()

    const links = wrapper.findAll('.dash-card-grid > a.dash-card')
    expect(links.map((a) => a.attributes('href'))).toEqual(['/projects', '/designer', '/git', '/ai', '/settings'])

    const quick = wrapper.get('[data-card-key="quick"]')
    expect(quick.element.tagName).toBe('ARTICLE')
    expect(quick.attributes('aria-disabled')).toBe('true')
    expect(quick.attributes('href')).toBeUndefined()
    expect(quick.classes()).toContain('is-unavailable')
  })

  it('navigates through the router when a card is clicked', async () => {
    const { wrapper, router } = await mountDashboard()

    await wrapper.get('[data-card-key="designer"]').trigger('click')
    await new Promise((r) => setTimeout(r, 0))

    expect(router.currentRoute.value.fullPath).toBe('/designer')
  })

  it('keeps the four right-hand panels but shows no invented rows or counts', async () => {
    const { wrapper } = await mountDashboard()

    const panels = wrapper.findAll('.dash-rightbar .dash-sidepanel')
    expect(panels.map((p) => p.attributes('data-panel-key'))).toEqual(['recent_projects', 'recent_groups', 'attention', 'notices'])
    expect(panels.map((p) => p.get('h2').text())).toEqual(['최근 프로젝트', '최근 그룹', '주의 필요', '시스템 알림'])
    for (const panel of panels) {
      expect(panel.get('.dash-panel-status').text()).toBe('준비 중')
      expect(panel.get('.dash-panel-empty').text()).toContain('아직 연결되지 않았습니다')
      // None of the mockup's sample data (project names, "2시간 전", counts 3/1/2/4).
      expect(panel.text()).not.toMatch(/\d/)
    }
    expect(wrapper.get('.dash-footer-mark').text()).toBe('Build the Flow, Not Just the Document.')
  })

  it('shows the tip without promising a search that does not exist, and lets it be dismissed', async () => {
    const { wrapper } = await mountDashboard()

    const tip = wrapper.get('.dash-tip')
    expect(tip.get('strong').text()).toBe('빠른 팁')
    expect(tip.text()).toContain('준비 중')

    const close = tip.get('button.dash-tip-close')
    expect(close.attributes('aria-label')).toBe('팁 닫기')
    await close.trigger('click')
    expect(wrapper.find('.dash-tip').exists()).toBe(false)
  })

  it('makes no API request (no project/Git/AI data is fetched by the Dashboard)', async () => {
    const { wrapper } = await mountDashboard()
    await new Promise((r) => setTimeout(r, 0))

    expect(blockedRequests).toEqual([])
    wrapper.unmount()
  })

  it('has the same gui.* keys in ko, en and ja', async () => {
    const { APP_MESSAGES } = await import('@/app/i18n')
    const keys = (obj: object, prefix = ''): string[] =>
      Object.entries(obj).flatMap(([k, v]) => (v && typeof v === 'object' ? keys(v, `${prefix}${k}.`) : [`${prefix}${k}`]))
    const ko = keys(APP_MESSAGES.ko).sort()
    expect(keys(APP_MESSAGES.en).sort()).toEqual(ko)
    expect(keys(APP_MESSAGES.ja).sort()).toEqual(ko)
  })
})
