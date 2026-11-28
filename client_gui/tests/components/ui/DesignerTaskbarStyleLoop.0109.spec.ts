import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import { getRequest } from '@shared/api'
import { registerAppMessages } from '@/app/i18n'
import DesignerTaskbar from '@/features/designer/shell/DesignerTaskbar.vue'
import { modelessStyle } from '@/core/overlay/modelessRegistry'
import { setCurrentUser } from '@/core/auth/currentUser'

// v02test.0109: the taskbar flickered between the saved style and the FlowGate default. Every /auth/me re-read
// (permission refresh after a request) handed out a new user object; the taskbar reset the style and read
// ui-settings again, which triggered another refresh — a loop. The style must follow the person, not the object.
vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(async () => ({ data: { settings: { designer_taskbar_style: 'modern' } } })) }))
registerAppMessages(i18n)
const mounted: VueWrapper[] = []
afterEach(() => { mounted.splice(0).forEach(w => w.unmount()); document.body.innerHTML = ''; modelessStyle.value = 'flowgate'; setCurrentUser(null); vi.mocked(getRequest).mockClear() })
function bar() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  const wrapper = mount(DesignerTaskbar, { global: { plugins: [i18n, router] }, attachTo: document.body })
  mounted.push(wrapper); return wrapper
}
const settingsReads = () => vi.mocked(getRequest).mock.calls.filter(([url]) => String(url).endsWith('/me/ui-settings')).length

describe('taskbar style follows the person, not the user object', () => {
  it('keeps the saved style and does not read ui-settings again when the same person is published again', async () => {
    setCurrentUser({ username: 'admin', email: null, isAdmin: true })
    const wrapper = bar()
    await flushPromises()
    expect(modelessStyle.value).toBe('modern')
    expect(settingsReads()).toBe(1)
    const seen: string[] = []
    const stop = (await import('vue')).watch(modelessStyle, (value) => seen.push(value), { flush: 'sync' })
    setCurrentUser({ username: 'admin', email: null, isAdmin: true })
    await flushPromises()
    stop()
    expect(seen).toEqual([])
    expect(settingsReads()).toBe(1)
    expect(wrapper.get('[data-test="designer-taskbar"]').attributes('data-taskbar-style')).toBe('modern')
  })
  it('goes back to the default and reads again when another person signs in', async () => {
    setCurrentUser({ username: 'admin', email: null, isAdmin: true })
    bar()
    await flushPromises()
    vi.mocked(getRequest).mockResolvedValueOnce({ data: { settings: { designer_taskbar_style: 'classic' } } } as never)
    setCurrentUser({ username: 'other', email: null, isAdmin: false })
    await flushPromises()
    expect(settingsReads()).toBe(2)
    expect(modelessStyle.value).toBe('classic')
  })
})
