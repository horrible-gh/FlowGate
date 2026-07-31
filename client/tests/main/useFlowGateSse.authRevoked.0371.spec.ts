import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { useFlowGateSse } from '@main/composables/useFlowGateSse'

// 0371 T0012 (NR0007 §4). The SSE stream used to be validated only at connect, so a
// session revoked elsewhere kept receiving pushes. The server now re-checks on every
// event and heartbeat and ends the stream with a terminal `auth_revoked` frame.
//
// The client half matters just as much: an ordinary close is indistinguishable from a
// network drop, and the composable's own reconnect loop (group 0021 / NR0003) would
// rebuild the stream every second, then every two, forever — each attempt 401ing on the
// same revoked session. So `auth_revoked` has to stop the loop and tell the user, which
// is what these tests pin.

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
})

afterEach(() => {
  vi.useRealTimers()
  delete (window as any).__accessToken__
})

describe('useFlowGateSse auth_revoked', () => {
  it('closes the stream and stops reconnecting when the server revokes the login', () => {
    vi.useFakeTimers()
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const first = MockEventSource.last
    first.emit('open')

    first.emit('auth_revoked', { reason: 'session_revoked' })

    expect(first.closed).toBe(true)
    // Well past the 30s backoff ceiling and the 75s liveness window: nothing must
    // rebuild a stream the server has already refused.
    vi.advanceTimersByTime(300000)
    expect(MockEventSource.instances).toHaveLength(1)
    wrapper.unmount()
  })

  it('tells the user their sign-in ended instead of failing silently', () => {
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    MockEventSource.last.emit('open')

    MockEventSource.last.emit('auth_revoked', { reason: 'user_inactive' })

    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.notifications.session_revoked'),
      'error',
    )
    wrapper.unmount()
  })

  it('survives a frame with no parseable payload', () => {
    vi.useFakeTimers()
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const first = MockEventSource.last
    first.emit('open')

    first.emit('auth_revoked') // no data at all

    expect(first.closed).toBe(true)
    vi.advanceTimersByTime(300000)
    expect(MockEventSource.instances).toHaveLength(1)
    wrapper.unmount()
  })

  it('still reconnects after an ordinary error (the stop is specific to auth_revoked)', () => {
    vi.useFakeTimers()
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const first = MockEventSource.last
    first.emit('open')

    first.emit('error')
    vi.advanceTimersByTime(1000)

    expect(MockEventSource.instances).toHaveLength(2)
    expect(showToast).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
