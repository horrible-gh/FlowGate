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
  modelessStyle.value = 'flowgate'
  vi.mocked(getRequest).mockResolvedValue({ data: { settings: { designer_taskbar_style: 'future' } } } as never)
  vi.mocked(patchRequest).mockImplementation(async (_path, body) => ({ data: { settings: { ...(body as object) } } }) as never)
})
function view() { return mount(MeThemeSettingsView, { global: { plugins: [i18n], stubs: { SettingsShell: { template: '<div><slot /></div>' }, SettingsSubnav: true, SettingsIcon: true } }, attachTo: document.body }) }
const cards = (w: ReturnType<typeof view>) => w.findAll('[data-test="taskbar-style-card"]')
const checked = (w: ReturnType<typeof view>) => cards(w).filter(c => c.attributes('aria-checked') === 'true').map(c => c.attributes('data-style'))
describe('personal taskbar style', () => {
  it('shows one preview card per implemented style, with no dropdown, save button or saved wording', async () => {
    const w = view(); await flushPromises()
    expect(w.find('select').exists()).toBe(false)
    expect(w.get('[role="radiogroup"]').attributes('aria-label')).toBe(String(i18n.global.t('gui.taskbar.style')))
    expect(cards(w).map(c => c.text().replace('✓', '').trim())).toEqual(['FlowGate', 'Modern', 'Dock', 'Classic'])
    expect(cards(w).every(c => c.find('.pv').exists())).toBe(true)
    // An unknown stored value falls back to FlowGate.
    expect(checked(w)).toEqual(['flowgate'])
    expect(w.findAll('button').filter(b => !b.attributes('data-test'))).toHaveLength(0)
    expect(w.find('[role="status"]').exists()).toBe(false)
    w.unmount()
  })
  it('applies a pressed card at once and saves only the style, without any wording', async () => {
    const w = view(); await flushPromises()
    await w.get('[data-style="dock"]').trigger('click')
    expect(modelessStyle.value).toBe('dock')
    expect(checked(w)).toEqual(['dock'])
    await flushPromises()
    expect(patchRequest).toHaveBeenCalledTimes(1)
    expect(patchRequest).toHaveBeenCalledWith('/api/v1/me/ui-settings', { designer_taskbar_style: 'dock' })
    expect(w.find('[role="status"]').exists()).toBe(false)
    expect(w.find('[role="alert"]').exists()).toBe(false)
    // Pressing the card that is already chosen sends nothing.
    await w.get('[data-style="dock"]').trigger('click'); await flushPromises()
    expect(patchRequest).toHaveBeenCalledTimes(1)
    w.unmount()
  })
  it('writes only the last of several quick choices, in order', async () => {
    // The first save is still on its way while the next two cards are pressed.
    let release: () => void = () => {}
    vi.mocked(patchRequest).mockImplementationOnce((_path, body) => new Promise(resolve => { release = () => resolve({ data: { settings: { ...(body as object) } } } as never) }))
    const w = view(); await flushPromises()
    await w.get('[data-style="modern"]').trigger('click')
    await w.get('[data-style="dock"]').trigger('click')
    await w.get('[data-style="classic"]').trigger('click')
    await flushPromises()
    expect(patchRequest).toHaveBeenCalledTimes(1)
    expect(checked(w)).toEqual(['classic'])
    // The late answer for the first choice does not take the screen back to it.
    release(); await flushPromises()
    expect(vi.mocked(patchRequest).mock.calls.map(c => (c[1] as Record<string, string>).designer_taskbar_style)).toEqual(['modern', 'classic'])
    expect(modelessStyle.value).toBe('classic')
    expect(checked(w)).toEqual(['classic'])
    w.unmount()
  })
  it('moves between the cards with the arrow keys, applying each one', async () => {
    const w = view(); await flushPromises()
    const group = w.get('[role="radiogroup"]')
    await group.trigger('keydown', { key: 'ArrowRight' }); await flushPromises()
    expect(checked(w)).toEqual(['modern'])
    expect(document.activeElement?.getAttribute('data-style')).toBe('modern')
    expect(w.get('[data-style="modern"]').attributes('tabindex')).toBe('0')
    expect(w.get('[data-style="flowgate"]').attributes('tabindex')).toBe('-1')
    await group.trigger('keydown', { key: 'ArrowLeft' }); await flushPromises()
    await group.trigger('keydown', { key: 'ArrowLeft' }); await flushPromises()
    expect(checked(w)).toEqual(['classic'])
    expect(modelessStyle.value).toBe('classic')
    w.unmount()
  })
  it('puts the saved style back and says so only when the save fails, then permits retry', async () => {
    vi.mocked(patchRequest).mockRejectedValueOnce(new Error('offline'))
    const w = view(); await flushPromises()
    await w.get('[data-style="classic"]').trigger('click'); await flushPromises()
    expect(w.get('[role="alert"]').text()).toBeTruthy()
    expect(checked(w)).toEqual(['flowgate'])
    expect(modelessStyle.value).toBe('flowgate')
    await w.get('[data-style="classic"]').trigger('click'); await flushPromises()
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(checked(w)).toEqual(['classic'])
    w.unmount()
  })
  it('shows a failed read and retries before permitting a choice', async () => {
    vi.mocked(getRequest).mockRejectedValueOnce(new Error('offline'))
    const w = view(); await flushPromises()
    expect(cards(w)).toHaveLength(0)
    expect(w.find('[role="alert"]').exists()).toBe(true)
    await w.get('button').trigger('click'); await flushPromises()
    expect(cards(w)).toHaveLength(4)
    expect(patchRequest).not.toHaveBeenCalled()
    w.unmount()
  })
})
