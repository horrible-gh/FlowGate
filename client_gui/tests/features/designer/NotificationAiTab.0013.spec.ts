import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import NotificationAiTab from '@/features/designer/top/notifications/NotificationAiTab.vue'
import { aiNotificationState, aiNotificationTitle, relativeNotificationTime } from '@/features/designer/top/notifications/notificationAiModel'
const mocks = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest: mocks.get, postRequest: vi.fn() }))
registerAppMessages(i18n)
let wrapper: VueWrapper<any> | null = null
const item = { run_id: 'run/1', doc_ref: 'p.m.1.0002-TR', doc_title: 'Report', succeeded: true, last_message_excerpt: 'Saved', finished_at: '2026-11-15T12:34:00Z' }
beforeEach(() => {
  mocks.get.mockReset()
  mocks.get.mockResolvedValue({ data: { ...item, provider_name: 'AI One', stop_reason: 'Complete', last_message: 'Full result' } })
  vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(new Date('2026-11-15T12:35:00Z'))
  i18n.global.locale.value = 'ko'
})
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.useRealTimers(); document.body.innerHTML = ''; i18n.global.locale.value = 'ko' })
function show() { wrapper = mount(NotificationAiTab, { props: { items: [item] }, global: { plugins: [i18n] } }); return wrapper }
describe('AI notification rows and details', () => {
  it('keeps success, failure, confirmed cancellation and unknown distinct', () => {
    expect(aiNotificationState(item)).toBe('success')
    expect(aiNotificationState({ run_id: 'r', succeeded: false })).toBe('failure')
    expect(aiNotificationState({ run_id: 'r', succeeded: false, stop_code: 'cancelled' })).toBe('stopped')
    expect(aiNotificationState({ run_id: 'r', stop_code: 'future_code' })).toBe('unknown')
    expect(aiNotificationTitle({ run_id: 'r', doc_ref: 'p.m.1.0002-TR' })).toBe('1.0002-TR')
    expect(relativeNotificationTime('invalid', 'ko')).toBeNull()
  })
  it.each(['ko', 'en', 'ja'] as const)('renders accessible icons and relative time in %s', (locale) => {
    i18n.global.locale.value = locale
    const w = show()
    const label = i18n.global.t('gui.notification_ai.detail')
    expect(w.get('.ai-detail').attributes('aria-label')).toBe(label)
    expect(w.get('.ai-detail').attributes('title')).toBe(label)
    expect(w.get('time').text()).toBe(new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(-1, 'minute'))
    expect(w.get('time').attributes('datetime')).toBe(item.finished_at)
    expect(w.find('[data-test="notification-ai-detail"]').exists()).toBe(false)
    expect(mocks.get).not.toHaveBeenCalled()
  })
  it.each(['.ai-row', '.ai-detail'])('opens server detail through %s and preserves document navigation', async (selector) => {
    const w = show()
    await w.get(selector).trigger('click'); await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('/api/v1/ai-invoke/run%2F1')
    const panel = document.querySelector('[data-test="notification-ai-detail"]')!
    expect(panel.textContent).toContain('Full result')
    expect(panel.textContent).toContain('AI One')
    expect(panel.textContent).toContain('Complete')
    ;(panel.querySelector('button') as HTMLButtonElement).click()
    await flushPromises()
    expect(w.emitted('open-document')).toEqual([[item.doc_ref]])
  })
  it('shows failure and retries instead of reusing a previous detail', async () => {
    mocks.get.mockRejectedValueOnce(new Error('403'))
    const w = show(); await w.get('.ai-detail').trigger('click'); await flushPromises()
    const alert = document.querySelector('[data-test="notification-ai-detail"] [role="alert"]')!
    expect(alert).not.toBeNull()
    ;(alert.querySelector('button') as HTMLButtonElement).click(); await flushPromises()
    expect(mocks.get).toHaveBeenCalledTimes(2)
    expect(document.querySelector('[data-test="notification-ai-detail"]')!.textContent).toContain('Full result')
  })
  it('keeps unknown codes out of the summary text but available in its tooltip', async () => {
    const w = show(); await w.setProps({ items: [{ run_id: 'r', outcome: 'future_code' }] })
    expect(w.get('.ai-summary').text()).not.toContain('future_code')
    expect(w.get('.ai-summary').attributes('title')).toBe('future_code')
  })
})
