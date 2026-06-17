import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { useFlowGateSse } from '@main/composables/useFlowGateSse'

// Regression (group 0021 / NR0003 items 1, 2): the SSE EventSource pins the access
// token into its url at connect time and the native auto-reconnect reuses that stale
// token (and gives up on a fatal 401). After an Axios token rotation or a drop past
// token expiry the stream silently died, so open documents stopped receiving
// workflow-decision events. The composable now drives reconnection itself, rebuilding
// the url with the freshest token, and reconnects immediately on token rotation.

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

describe('useFlowGateSse reconnection', () => {
  it('connects with the current token in the url', () => {
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    expect(MockEventSource.instances).toHaveLength(1)
    expect(MockEventSource.last.url).toContain('token=tok1')
    wrapper.unmount()
  })

  it('keeps a healthy stream open on token rotation, then uses the fresh token on the next reconnect', () => {
    // group 0028 T0004: the SSE server only checks the token at connect time, so an open
    // stream stays valid across rotations. With proactive refresh rotating ~once per token
    // lifetime, tearing the stream down on every rotation would cause a resync storm; instead
    // the live stream is left intact and the rotated token is picked up on the next genuine
    // reconnect.
    vi.useFakeTimers()
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const first = MockEventSource.last
    first.emit('open')

    // Axios rotated the token and broadcast the rotation event while the stream is healthy.
    ;(window as any).__accessToken__ = 'tok2'
    window.dispatchEvent(
      new CustomEvent('fg:access_token_refreshed', { detail: { token: 'tok2' } }),
    )

    // No churn: the open stream is left alone.
    expect(first.closed).toBe(false)
    expect(MockEventSource.instances).toHaveLength(1)

    // When the stream later drops, the reconnect rebuilds the url with the freshest token.
    first.emit('error')
    vi.advanceTimersByTime(1000)
    expect(MockEventSource.instances).toHaveLength(2)
    expect(MockEventSource.last.url).toContain('token=tok2')
    wrapper.unmount()
  })

  it('takes over reconnection on error using a backoff timer', () => {
    vi.useFakeTimers()
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const first = MockEventSource.last
    first.emit('open')

    // Connection drops; token rotated in the meantime.
    ;(window as any).__accessToken__ = 'tok3'
    first.emit('error')
    expect(first.closed).toBe(true)
    // No immediate reconnect — it is scheduled with backoff.
    expect(MockEventSource.instances).toHaveLength(1)

    vi.advanceTimersByTime(1000)
    expect(MockEventSource.instances).toHaveLength(2)
    expect(MockEventSource.last.url).toContain('token=tok3')
    wrapper.unmount()
  })

  it('forces a full resync when a dropped connection recovers', () => {
    vi.useFakeTimers()
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const first = MockEventSource.last
    first.emit('open')
    expect(refreshAll).not.toHaveBeenCalled() // first open: no resync

    first.emit('error')
    vi.advanceTimersByTime(1000)
    MockEventSource.last.emit('open') // reconnected
    expect(refreshAll).toHaveBeenCalledTimes(1) // recovery: resync fired
    wrapper.unmount()
  })

  it('stops reconnecting after unmount', () => {
    vi.useFakeTimers()
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const first = MockEventSource.last
    first.emit('open')
    wrapper.unmount()

    first.emit('error')
    vi.advanceTimersByTime(60000)
    // No new connection after unmount.
    expect(MockEventSource.instances).toHaveLength(1)
  })

  // Liveness watchdog (group 0025 TR): a silently-dead stream never fires `error`, so the
  // NR0003 reconnection alone leaves it a zombie. The server now emits a named `ping`
  // heartbeat and the composable force-reconnects when it stops arriving.
  it('keeps the connection alive while heartbeats arrive (no reconnect)', () => {
    vi.useFakeTimers()
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const first = MockEventSource.last
    first.emit('open')

    // A ping every 30s keeps the stream healthy across the stale window.
    for (let i = 0; i < 6; i++) {
      vi.advanceTimersByTime(30000)
      first.emit('ping')
    }
    expect(MockEventSource.instances).toHaveLength(1)
    wrapper.unmount()
  })

  it('forces a reconnect when the heartbeat stops (silent death)', () => {
    vi.useFakeTimers()
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const first = MockEventSource.last
    first.emit('open')

    // No ping ever arrives — zombie stream, no `error` fired. Past the stale window the
    // watchdog (15s tick, 75s threshold) must tear it down and rebuild.
    vi.advanceTimersByTime(95000)
    expect(first.closed).toBe(true)
    expect(MockEventSource.instances).toHaveLength(2)
    wrapper.unmount()
  })

  it('reconnects on tab becoming visible after a silent stall', () => {
    vi.useFakeTimers()
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const first = MockEventSource.last
    first.emit('open')

    // Jump the clock forward past the stale window without ticking the periodic watchdog
    // (setSystemTime does not fire timers), so the visibility handler is what drives
    // recovery. jsdom defaults document.visibilityState to 'visible'.
    vi.setSystemTime(Date.now() + 80000)
    document.dispatchEvent(new Event('visibilitychange'))

    expect(first.closed).toBe(true)
    expect(MockEventSource.instances).toHaveLength(2)
    wrapper.unmount()
  })

  it('reconnects immediately when the network comes back online', () => {
    vi.useFakeTimers()
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const first = MockEventSource.last
    first.emit('open')

    ;(window as any).__accessToken__ = 'tok-online'
    window.dispatchEvent(new Event('online'))

    expect(first.closed).toBe(true)
    expect(MockEventSource.instances).toHaveLength(2)
    expect(MockEventSource.last.url).toContain('token=tok-online')
    wrapper.unmount()
  })
})
