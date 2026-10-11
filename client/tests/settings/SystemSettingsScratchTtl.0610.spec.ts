import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

const { getRequest, patchRequest, postFormRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), patchRequest: vi.fn(), postFormRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, patchRequest, postFormRequest }))

const rows: Record<string, string> = {}
const originalLocale = i18n.global.locale.value

async function mountView() {
  setActivePinia(createPinia())
  const SystemSettingsView = (await import('@/settings/views/system/SystemSettingsView.vue')).default
  const wrapper = mount(SystemSettingsView, { global: { plugins: [i18n] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  for (const key of Object.keys(rows)) delete rows[key]
  getRequest.mockReset()
  patchRequest.mockReset()
  postFormRequest.mockReset()
  i18n.global.locale.value = 'en'
  getRequest.mockImplementation((url: string) => {
    if (url === '/api/v1/system/info') return Promise.resolve({ data: {} })
    if (url === '/api/v1/system/settings') return Promise.resolve({
      data: { settings: Object.entries(rows).map(([setting_key, setting_value]) => ({ setting_key, setting_value })) },
    })
    return Promise.reject(new Error(`unexpected GET ${url}`))
  })
  patchRequest.mockImplementation((_url: string, body: { updates: Record<string, string> }) => {
    Object.assign(rows, body.updates)
    return Promise.resolve({ data: { updated: [] } })
  })
})

describe('system Scratch TTL value and unit', () => {
  it.each([
    ['1', 'minute'], ['30', 'minute'], ['6', 'hour'], ['1', 'day'], ['7', 'day'],
  ])('saves and re-fetches %s %s without converting the chosen unit', async (value, unit) => {
    const view = await mountView()
    expect((view.get('#scratch-ttl-value').element as HTMLInputElement).value).toBe('7')
    expect((view.get('#scratch-ttl-unit').element as HTMLSelectElement).value).toBe('day')
    await view.get('#scratch-ttl-value').setValue(value)
    await view.get('#scratch-ttl-unit').setValue(unit)
    await view.get('[data-test="system-settings-save"]').trigger('click')
    await flushPromises()
    expect(patchRequest).toHaveBeenCalledWith('/api/v1/system/settings', {
      updates: expect.objectContaining({ scratch_ttl_value: value, scratch_ttl_unit: unit }),
    })
    view.unmount()
    const loaded = await mountView()
    expect((loaded.get('#scratch-ttl-value').element as HTMLInputElement).value).toBe(value)
    expect((loaded.get('#scratch-ttl-unit').element as HTMLSelectElement).value).toBe(unit)
    loaded.unmount()
  })

  it.each(['0', '-1', '1.5', ''])('rejects invalid numeric input %s before PATCH', async value => {
    const view = await mountView()
    await view.get('#scratch-ttl-value').setValue(value)
    await view.get('[data-test="system-settings-save"]').trigger('click')
    await flushPromises()
    expect(patchRequest).not.toHaveBeenCalled()
    expect(view.get('[role="alert"]').text()).toBe(i18n.global.t('settings.system.scratch_ttl.invalid'))
    view.unmount()
  })

  it('rejects an unsupported stored unit before PATCH', async () => {
    rows.scratch_ttl_value = '1'
    rows.scratch_ttl_unit = 'week'
    const view = await mountView()
    await view.get('[data-test="system-settings-save"]').trigger('click')
    await flushPromises()
    expect(patchRequest).not.toHaveBeenCalled()
    expect(view.get('[role="alert"]').text()).toBe(i18n.global.t('settings.system.scratch_ttl.invalid'))
    view.unmount()
  })

  it.each([
    ['ko', ['분', '시간', '일']],
    ['ja', ['分', '時間', '日']],
    ['en', ['Minutes', 'Hours', 'Days']],
  ])('renders the three unit choices in %s', async (locale, labels) => {
    i18n.global.locale.value = locale as 'ko' | 'ja' | 'en'
    const view = await mountView()
    expect(view.findAll('#scratch-ttl-unit option').map(option => option.text())).toEqual(labels)
    view.unmount()
    i18n.global.locale.value = originalLocale
  })
})
