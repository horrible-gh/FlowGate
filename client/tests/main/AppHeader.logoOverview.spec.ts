import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import AppHeader from '@main/components/AppHeader.vue'
import { useTabsStore } from '@main/stores/tabs'

function mountHeader() {
  return mount(AppHeader, {
    global: {
      plugins: [i18n],
      stubs: {
        // RouterLink to "/" is a no-op once already on the dashboard route, so it
        // is stubbed to a plain anchor; the overview behaviour is driven by the
        // brand's own @click handler, not by navigation.
        RouterLink: { template: '<a><slot /></a>' },
        ProjectSelector: true,
      },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  delete window.__accessToken__
  i18n.global.locale.value = 'en'
})

describe('AppHeader brand → Overview tab (R0001: 로고클릭)', () => {
  it('clears the active document tab so the overview panel shows', async () => {
    const store = useTabsStore()
    store.tabs = [{ id: 'flowgate.default.0039.0001-R', title: 'R', path: '', type: 'md' }]
    store.activeTabId = 'flowgate.default.0039.0001-R'

    const wrapper = mountHeader()
    await wrapper.get('.header-brand').trigger('click')

    expect(store.activeTabId).toBeNull()
  })

  it('is idempotent when already on the overview tab', async () => {
    const store = useTabsStore()
    store.activeTabId = null

    const wrapper = mountHeader()
    await wrapper.get('.header-brand').trigger('click')

    expect(store.activeTabId).toBeNull()
  })
})
