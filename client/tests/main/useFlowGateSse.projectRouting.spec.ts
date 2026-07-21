import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { useFlowGateSse } from '@main/composables/useFlowGateSse'
import { useProjectStore } from '@main/stores/project'

// 0291 D0005 §3-2/§3-3. The server routes broadcast events by project, but it only
// learns which project a screen is looking at from the stream url at connect time.
// So the client must (a) put its current project in that url and (b) rebuild the
// stream when the user switches project — and because events for the new project
// that arrive during the switch are simply not delivered, the reconnect must also
// re-read the screen, or the performance fix becomes a "sometimes it doesn't
// refresh" bug.

const { showToast } = vi.hoisted(() => ({ showToast: vi.fn() }))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  readyState = 0
  closed = false
  listeners = new Map<string, (event: Event) => void>()

  constructor(url: string, _options?: EventSourceInit) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.set(type, listener as (event: Event) => void)
  }

  emit(type: string, data?: object) {
    this.listeners.get(type)?.({ data: data ? JSON.stringify(data) : '' } as MessageEvent)
  }

  close() {
    this.closed = true
    this.readyState = 2
  }

  static get last(): MockEventSource {
    return MockEventSource.instances[MockEventSource.instances.length - 1]
  }
}

const Harness = defineComponent({
  setup() {
    return useFlowGateSse(refreshAll)
  },
  template: '<div />',
})

let refreshAll: ReturnType<typeof vi.fn>

beforeEach(() => {
  setActivePinia(createPinia())
  MockEventSource.instances = []
  showToast.mockReset()
  refreshAll = vi.fn()
  vi.stubGlobal('EventSource', MockEventSource)
  ;(window as any).__accessToken__ = 'tok1'
  sessionStorage.clear()
  localStorage.clear()
})

afterEach(() => {
  vi.useRealTimers()
  delete (window as any).__accessToken__
  localStorage.clear()
})

describe('useFlowGateSse project routing', () => {
  it('declares the current project in the stream url', () => {
    localStorage.setItem('fg_current_project_id', 'proj_alpha')
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })

    expect(MockEventSource.last.url).toContain('project=proj_alpha')
    expect(MockEventSource.last.url).toContain('token=tok1')
    wrapper.unmount()
  })

  it('omits the project when none is selected yet', () => {
    // No project chosen → no filter declared → the server falls back to delivering
    // everything, so nothing is missed during app start-up.
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })

    expect(MockEventSource.last.url).not.toContain('project=')
    wrapper.unmount()
  })

  it('reconnects with the new project when the user switches', async () => {
    localStorage.setItem('fg_current_project_id', 'proj_alpha')
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const first = MockEventSource.last
    first.emit('open')

    useProjectStore().setCurrentProject('proj_beta')
    await nextTick()

    expect(first.closed).toBe(true)
    expect(MockEventSource.instances).toHaveLength(2)
    expect(MockEventSource.last.url).toContain('project=proj_beta')
    wrapper.unmount()
  })

  it('re-reads the screen after the project-switch reconnect opens', async () => {
    // §3-3: the gap between the old subscription ending and the new one being
    // registered drops events. The full re-read on open is what makes that gap
    // harmless — without it this change would produce missed updates.
    localStorage.setItem('fg_current_project_id', 'proj_alpha')
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    MockEventSource.last.emit('open')
    expect(refreshAll).not.toHaveBeenCalled()

    useProjectStore().setCurrentProject('proj_beta')
    await nextTick()
    MockEventSource.last.emit('open')

    expect(refreshAll).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('does not reconnect when the project is set to the value already connected', async () => {
    localStorage.setItem('fg_current_project_id', 'proj_alpha')
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    MockEventSource.last.emit('open')

    useProjectStore().setCurrentProject('proj_alpha')
    await nextTick()

    expect(MockEventSource.instances).toHaveLength(1)
    wrapper.unmount()
  })

  it('ignores a project change after unmount', async () => {
    localStorage.setItem('fg_current_project_id', 'proj_alpha')
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    MockEventSource.last.emit('open')
    wrapper.unmount()

    useProjectStore().setCurrentProject('proj_beta')
    await nextTick()

    expect(MockEventSource.instances).toHaveLength(1)
  })
})
