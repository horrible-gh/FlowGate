import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AppHeader from '@main/components/AppHeader.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  serverLogout: vi.fn(),
}))

function mountHeader() {
  return mount(AppHeader, {
    attachTo: document.body,
    global: {
      plugins: [i18n],
      stubs: {
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
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockResolvedValue({ data: {} })
})

// CH0009: the floating monitor kept colliding with whatever each screen fixes to its
// bottom edge (the chat composer, the sticky action bar). It now rides in the header
// beside the provider selector, where overlap is structurally impossible.
describe('AppHeader run monitor chip (0269 NR0011)', () => {
  it('renders the run monitor inside the header, immediately before the provider selector', () => {
    const wrapper = mountHeader()

    const monitor = wrapper.find('[data-test="ai-miniplayer"]')
    expect(monitor.exists()).toBe(true)
    expect(wrapper.find('[data-test="ai-miniplayer-chip"]').exists()).toBe(true)

    // Order inside the header: run monitor → provider selector → spacer.
    const header = wrapper.get('.app-header').element
    const children = Array.from(header.children)
    const providerIdx = children.findIndex(el => el.classList.contains('ai-provider-selector'))
    const monitorIdx = children.indexOf(monitor.element)
    const spacerIdx = children.findIndex(el => el.classList.contains('header-spacer'))

    expect(monitorIdx).toBeGreaterThanOrEqual(0)
    expect(providerIdx).toBe(monitorIdx + 1)
    expect(spacerIdx).toBe(providerIdx + 1)

    wrapper.unmount()
  })

  // rev1 반려: the selector carried its own robot icon, so an identical glyph sat on
  // each side of the select box. Exactly one robot may flank the selector, and it is
  // the chip.
  it('leaves the provider selector without an icon of its own', () => {
    const wrapper = mountHeader()

    const selector = wrapper.get('.ai-provider-selector')
    expect(selector.findAll('.app-icon')).toHaveLength(0)
    expect(selector.findAll('svg, img, i')).toHaveLength(0)

    // The chip is the one and only icon rendered beside the select box.
    const chip = wrapper.get('[data-test="ai-miniplayer-chip"]')
    expect(chip.findAll('.app-icon')).toHaveLength(1)

    wrapper.unmount()
  })

  it('opens the run popover from the header chip and closes it on an outside click', async () => {
    const wrapper = mountHeader()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-hdr', group_id: 'flowgate.default.3100',
      doc_ref: 'flowgate.default.3100.0001-R', mode: 'continuous', docs_target: 4,
    })
    await flushPromises()

    await wrapper.get('[data-test="ai-miniplayer-chip"]').trigger('click')
    expect(wrapper.find('.aiv-mini__panel').exists()).toBe(true)
    expect(wrapper.find('.aiv-mini__card').exists()).toBe(true)

    // Clicking elsewhere in the header dismisses it — a header popover must not stick.
    wrapper.get('.header-brand').element.dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    )
    await flushPromises()
    expect(wrapper.find('.aiv-mini__panel').exists()).toBe(false)

    wrapper.unmount()
  })
})
