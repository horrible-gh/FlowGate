import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import MeThemeSettingsView from '@/features/settings/views/MeThemeSettingsView.vue'
import { modelessStyle } from '@/core/overlay/modelessRegistry'
import { getRequest, patchRequest } from '@shared/api'
vi.mock('@/features/settings/components/meNav', () => ({ useMeNav: () => [] }))
vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(), patchRequest: vi.fn() }))
registerAppMessages(i18n)
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getRequest).mockResolvedValue({ data: { settings: { designer_taskbar_style: 'future' } } } as never)
  vi.mocked(patchRequest).mockResolvedValue({ data: { settings: { designer_taskbar_style: 'flowgate' } } } as never)
})
function view() { return mount(MeThemeSettingsView, { global: { plugins: [i18n], stubs: { SettingsShell: { template: '<div><slot /></div>' }, SettingsSubnav: true, SettingsIcon: true } } }) }
describe('personal taskbar style', () => {
  it('shows only implemented choices and PATCHes only the style, updating shared appearance', async () => {
    const w = view(); await flushPromises()
    expect(w.findAll('option').map(o => o.text())).toEqual(['FlowGate'])
    expect((w.get('select').element as HTMLSelectElement).value).toBe('flowgate')
    await w.get('button').trigger('click'); await flushPromises()
    expect(patchRequest).toHaveBeenCalledTimes(1)
    expect(patchRequest).toHaveBeenCalledWith('/api/v1/me/ui-settings', { designer_taskbar_style: 'flowgate' })
    expect(modelessStyle.value).toBe('flowgate')
    expect(w.find('[role="alert"]').exists()).toBe(false)
    w.unmount()
  })
  it('reports a failed save and permits retry without overwriting other settings', async () => {
    vi.mocked(patchRequest).mockRejectedValueOnce(new Error('offline'))
    const w = view(); await flushPromises()
    await w.get('button').trigger('click'); await flushPromises()
    expect(w.get('[role="alert"]').text()).toBeTruthy()
    expect((w.get('select').element as HTMLSelectElement).value).toBe('flowgate')
    await w.get('button').trigger('click'); await flushPromises()
    expect(w.find('[role="alert"]').exists()).toBe(false)
    w.unmount()
  })
  it('shows a failed read and retries before permitting save', async () => {
    vi.mocked(getRequest).mockRejectedValueOnce(new Error('offline'))
    const w = view(); await flushPromises()
    expect(w.find('select').exists()).toBe(false)
    expect(w.find('[role="alert"]').exists()).toBe(true)
    await w.get('button').trigger('click'); await flushPromises()
    expect(w.find('select').exists()).toBe(true)
    expect(patchRequest).not.toHaveBeenCalled()
    w.unmount()
  })
})
