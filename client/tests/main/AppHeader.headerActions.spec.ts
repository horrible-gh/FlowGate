import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AppHeader from '@main/components/AppHeader.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  serverLogout: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

function mountHeader() {
  return mount(AppHeader, {
    attachTo: document.body,
    global: {
      plugins: [i18n],
      stubs: {
        RouterLink: { template: '<a><slot /></a>' },
        ProjectSelector: true,
        GitStatusPanel: true,
        teleport: true,
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

// 0471 D0008 §3.1 relocated Git + 알람 out of .header-nav, which had been supplying the
// spacing between every button and divider. .app-header is a gapless flex row, so dropped
// in bare they rendered flush against the project selector, the divider and each other
// (0020-TR rev3 rejection: "버튼은 선이랑 옆버튼이랑 다닥다닥 붙어있고"). The group must
// therefore stay inside a spacing container of its own.
describe('AppHeader relocated Git/alarm group (0471 D0008 §3.1)', () => {
  it('keeps Git and the bell inside .hdr-actions with a divider between them', () => {
    const wrapper = mountHeader()

    const group = wrapper.get('.hdr-actions')
    const children = Array.from(group.element.children)
    expect(children).toHaveLength(3)
    expect(children[0].classList.contains('git-menu-wrap')).toBe(true)
    expect(children[1].classList.contains('hdr-div')).toBe(true)
    expect(children[2].classList.contains('notif-center')).toBe(true)

    wrapper.unmount()
  })

  it('places the group between the project selector and the run monitor, with a trailing divider', () => {
    const wrapper = mountHeader()

    const header = wrapper.get('.app-header').element
    const children = Array.from(header.children)
    const groupIdx = children.findIndex(el => el.classList.contains('hdr-actions'))
    const monitorIdx = children.findIndex(
      el => el.getAttribute('data-test') === 'ai-miniplayer',
    )

    expect(groupIdx).toBeGreaterThanOrEqual(0)
    // group → divider → run monitor
    expect(children[groupIdx + 1].classList.contains('hdr-div')).toBe(true)
    expect(monitorIdx).toBe(groupIdx + 2)

    wrapper.unmount()
  })

  it('leaves no Git or bell button behind in the right-hand nav', () => {
    const wrapper = mountHeader()

    const nav = wrapper.get('.header-nav')
    expect(nav.find('.git-menu-wrap').exists()).toBe(false)
    expect(nav.find('.notif-center').exists()).toBe(false)

    wrapper.unmount()
  })
})
