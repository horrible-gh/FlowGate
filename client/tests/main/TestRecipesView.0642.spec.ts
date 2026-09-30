import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import TestRecipesView from '@/settings/views/project/TestRecipesView.vue'
import { useSettingsStore } from '@/settings/stores/settings.js'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  getRequest,
  patchRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  i18n.global.locale.value = 'en'
  getRequest.mockReset().mockImplementation((url: string) => {
    if (url.endsWith('/test-commands')) {
      return Promise.resolve({
        data: {
          data: [{
            id: 1,
            command: 'cd client && npm test',
            description: null,
            origin: 'tr2',
            last_success_at: '2026-09-30T12:00:00',
            updated_at: '2026-09-30T12:00:00',
          }],
        },
      })
    }
    if (url.endsWith('/engine-recipes')) return Promise.resolve({ data: { recipes: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('0642 TestRecipes origin=tr2', () => {
  it('renders the dedicated TR2 approval origin label', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useSettingsStore().setCurrentProject('flowgate')
    const wrapper = mount(TestRecipesView, { global: { plugins: [pinia, i18n] } })
    await flushPromises()
    expect(wrapper.text()).toContain('TR2 approval')
    expect(wrapper.find('.badge').text()).toBe('TR2 approval')
  })
})
