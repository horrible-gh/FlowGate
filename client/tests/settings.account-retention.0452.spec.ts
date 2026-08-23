// Group 0452 T0005 §3 — the settings screen that chooses the finished-card retention.
// Named after what it configures, the AI run monitor in the header, not after the
// sidebar group it sits in (TR0006 review).
//
// What is pinned here is the part a unit test can actually be wrong about: that the nine
// choices come from the SERVER's envelope rather than an array typed into the view, that
// the browser mirror is written only after a save the server accepted (an open monitor tab
// reads that mirror through a storage event — see aiInvokeRuns.spec.ts), and that a failed
// save confirms neither the selection nor the mirror.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import AiRunMonitorSettingsView from '../src/settings/views/AiRunMonitorSettingsView.vue'
import {
  RETENTION_DOMAIN_MINUTES,
  RETENTION_MIRROR_KEY,
  UI_SETTINGS_PATH,
} from '@shared/aiFinishedCardRetention'

const { getRequest, patchRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  patchRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, patchRequest }))

const FIELD = 'ai_finished_card_retention_minutes'
const originalLocale = i18n.global.locale.value

function envelope(minutes: number, domain: number[] = [...RETENTION_DOMAIN_MINUTES]) {
  return {
    data: {
      ok: true,
      settings: { [FIELD]: minutes, updated_at: null },
      is_default: minutes === 30,
      defaults: { [FIELD]: 30 },
      domain: { [FIELD]: domain },
    },
  }
}

async function mountView() {
  const wrapper = mount(AiRunMonitorSettingsView, { global: { plugins: [i18n] } })
  await flush()
  return wrapper
}

async function flush() {
  await Promise.resolve()
  await Promise.resolve()
  await nextTick()
}

const options = (wrapper: Awaited<ReturnType<typeof mountView>>) =>
  wrapper.findAll('#ai-finished-card-retention option')

describe('AiRunMonitorSettingsView — finished-card retention', () => {
  beforeEach(() => {
    localStorage.clear()
    getRequest.mockReset()
    patchRequest.mockReset()
    getRequest.mockResolvedValue(envelope(30))
    i18n.global.locale.value = 'en'
  })

  afterEach(() => {
    i18n.global.locale.value = originalLocale
    document.body.innerHTML = ''
  })

  it('draws the server domain, in the server order, and selects the stored value', async () => {
    getRequest.mockResolvedValueOnce(envelope(720))
    const wrapper = await mountView()

    expect(getRequest).toHaveBeenCalledWith(UI_SETTINGS_PATH)
    expect(options(wrapper).map((o) => Number(o.element.value))).toEqual([
      -1, 0, 30, 60, 120, 180, 360, 720, 1440,
    ])
    const select = wrapper.get('#ai-finished-card-retention').element as HTMLSelectElement
    expect(Number(select.value)).toBe(720)
  })

  it('follows a domain the server ships rather than a list of its own', async () => {
    // If the view carried its own nine values this would still render nine.
    getRequest.mockResolvedValueOnce(envelope(30, [0, 30, 1440]))
    const wrapper = await mountView()

    expect(options(wrapper).map((o) => Number(o.element.value))).toEqual([0, 30, 1440])
  })

  it('labels the two sentinels and the ordinary values in English', async () => {
    const wrapper = await mountView()
    const labels = options(wrapper).map((o) => o.text())

    expect(labels).toEqual([
      'Never', 'Immediately', '30 min',
      '1 hour', '2 hours', '3 hours', '6 hours', '12 hours', '24 hours',
    ])
  })

  it.each([
    ['ko', ['사라지지 않음', '바로 사라짐', '30분', '1시간', '24시간']],
    ['ja', ['消えない', 'すぐに消す', '30分', '1時間', '24時間']],
  ])('labels them in %s too', async (locale, expected) => {
    i18n.global.locale.value = locale as 'ko' | 'ja'
    const wrapper = await mountView()
    const labels = options(wrapper).map((o) => o.text())

    expect([labels[0], labels[1], labels[2], labels[3], labels[8]]).toEqual(expected)
  })

  it('repairs a stored value the server never repaired', async () => {
    getRequest.mockResolvedValueOnce(envelope(45))
    const wrapper = await mountView()

    const select = wrapper.get('#ai-finished-card-retention').element as HTMLSelectElement
    expect(Number(select.value)).toBe(30)
  })

  it('saves the chosen value, adopts the answer and writes the mirror', async () => {
    const wrapper = await mountView()
    patchRequest.mockResolvedValueOnce(envelope(-1))

    await wrapper.get('#ai-finished-card-retention').setValue('-1')
    await wrapper.get('button').trigger('click')
    await flush()

    expect(patchRequest).toHaveBeenCalledWith(UI_SETTINGS_PATH, { [FIELD]: -1 })
    // The mirror carries what the server answered with, not what was sent.
    expect(localStorage.getItem(RETENTION_MIRROR_KEY)).toBe('-1')
    expect(wrapper.text()).toContain(i18n.global.t('settings.ai_run_monitor.retention.saved'))
    expect(wrapper.find('.error').exists()).toBe(false)
  })

  it('adopts the server answer even when it differs from what was sent', async () => {
    const wrapper = await mountView()
    patchRequest.mockResolvedValueOnce(envelope(30))

    await wrapper.get('#ai-finished-card-retention').setValue('1440')
    await wrapper.get('button').trigger('click')
    await flush()

    const select = wrapper.get('#ai-finished-card-retention').element as HTMLSelectElement
    expect(Number(select.value)).toBe(30)
    expect(localStorage.getItem(RETENTION_MIRROR_KEY)).toBe('30')
  })

  it('leaves the mirror alone when the save fails', async () => {
    const wrapper = await mountView()
    patchRequest.mockRejectedValueOnce({ response: { status: 422 } })

    await wrapper.get('#ai-finished-card-retention').setValue('0')
    await wrapper.get('button').trigger('click')
    await flush()

    // A mirror written here would tell an open monitor tab to apply a setting that is not
    // stored anywhere, and nothing would ever correct it.
    expect(localStorage.getItem(RETENTION_MIRROR_KEY)).toBeNull()
    expect(wrapper.text()).toContain(i18n.global.t('settings.ai_run_monitor.retention.save_failed'))
    expect(wrapper.text()).not.toContain(i18n.global.t('settings.ai_run_monitor.retention.saved'))
  })

  it('still draws the choices when the lookup fails, and says what happened', async () => {
    getRequest.mockRejectedValueOnce(new Error('offline'))
    const wrapper = await mountView()

    expect(wrapper.find('.error').text()).toBe(
      i18n.global.t('settings.ai_run_monitor.retention.load_failed'),
    )
    expect(options(wrapper)).toHaveLength(RETENTION_DOMAIN_MINUTES.length)
  })

  it('disables the control while loading and again while saving', async () => {
    let settleGet: (value: unknown) => void = () => {}
    getRequest.mockImplementationOnce(() => new Promise((resolve) => { settleGet = resolve }))
    const wrapper = mount(AiRunMonitorSettingsView, { global: { plugins: [i18n] } })
    await nextTick()

    expect(wrapper.get('#ai-finished-card-retention').attributes('disabled')).toBeDefined()
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain(i18n.global.t('settings.ai_run_monitor.retention.loading'))

    settleGet(envelope(30))
    await flush()
    expect(wrapper.get('#ai-finished-card-retention').attributes('disabled')).toBeUndefined()

    let settlePatch: (value: unknown) => void = () => {}
    patchRequest.mockImplementationOnce(() => new Promise((resolve) => { settlePatch = resolve }))
    await wrapper.get('button').trigger('click')
    await nextTick()

    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    expect(wrapper.get('button').text()).toBe(i18n.global.t('settings.ai_run_monitor.retention.saving'))

    settlePatch(envelope(30))
    await flush()
    expect(wrapper.get('button').attributes('disabled')).toBeUndefined()
  })

  // The screen is only reachable if the router and the sidebar agree with it. A view
  // nobody can navigate to is the "half a change" this group has been refused for before.
  it('is wired into the settings router as the default entry point', () => {
    const router = readFileSync(
      resolve(__dirname, '../src/settings/router/index.js'),
      'utf8',
    )
    expect(router).toContain(
      "import AiRunMonitorSettingsView from '../views/AiRunMonitorSettingsView.vue'",
    )
    expect(router).toContain("{path:'ai-run-monitor',component:AiRunMonitorSettingsView}")
    expect(router).toContain("{path:'',redirect:'/settings/ai-run-monitor'}")
    // No permission meta: every signed-in user owns their own preferences.
    expect(router).not.toMatch(/path:'ai-run-monitor',component:AiRunMonitorSettingsView,meta:/)
  })

  it('appears in the account group of the sidebar alongside security and sessions', () => {
    const nav = readFileSync(
      resolve(__dirname, '../src/settings/components/SettingsNav.vue'),
      'utf8',
    )
    const accountGroup = nav.slice(
      nav.indexOf("t('settings.nav.account')"),
      nav.indexOf('nav-divider'),
    )
    expect(accountGroup).toContain('to="/settings/ai-run-monitor"')
    expect(accountGroup).toContain("t('settings.nav.ai_run_monitor')")
    // The existing entry stays where it was — this adds a sibling, it does not replace one.
    expect(accountGroup).toContain('to="/settings/security"')
    expect(accountGroup).toContain("t('settings.nav.security')")
  })

  // TR0006 was refused because the entry was called "계정 설정" — the name of the group it
  // sits in, which says nothing about what the screen does. The name has to be the thing it
  // configures, in all three locales, and it must not repeat the group heading.
  it.each([
    ['ko', 'AI 실행 모니터', '계정'],
    ['en', 'AI run monitor', 'Account'],
    ['ja', 'AI実行モニター', 'アカウント'],
  ])('names itself after the AI run monitor in %s, not after its sidebar group', (
    locale,
    expected,
    group,
  ) => {
    i18n.global.locale.value = locale as 'ko' | 'en' | 'ja'
    const navLabel = i18n.global.t('settings.nav.ai_run_monitor')
    const title = i18n.global.t('settings.ai_run_monitor.title')

    expect(navLabel).toBe(expected)
    expect(title).toBe(expected)
    expect(navLabel).not.toContain(i18n.global.t('settings.nav.account'))
    expect(navLabel).not.toBe(group)
    // The old key is gone, not merely shadowed by a new one.
    expect(i18n.global.te('settings.nav.account_settings')).toBe(false)
    expect(i18n.global.te('settings.account.title')).toBe(false)
  })

  it('keeps the label, the hint and the control wired together', async () => {
    const wrapper = await mountView()

    const label = wrapper.get('label.field-label')
    expect(label.attributes('for')).toBe('ai-finished-card-retention')
    expect(label.text()).toBe(i18n.global.t('settings.ai_run_monitor.retention.label'))
    expect(wrapper.get('#ai-finished-card-retention').attributes('aria-describedby'))
      .toBe('ai-finished-card-retention-hint')
    expect(wrapper.get('#ai-finished-card-retention-hint').text())
      .toBe(i18n.global.t('settings.ai_run_monitor.retention.hint'))
  })
})
