import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import axios, { AxiosError } from 'axios'
import i18n from '@shared/i18n'
import api from '@shared/api'
import { registerAppMessages } from '@/app/i18n'
import ConnectionBanner from '@/app/components/ConnectionBanner.vue'
import { createConnectionRecovery } from '@shared/connectionRecovery'
import { connectionLost, setConnectionLost } from '@shared/connectionStatus'
import { SELF_HEAL_DELAYS_MS, useSelfHeal } from '@shared/selfHeal'

// 0060 (13:22 again): a failed panel waited for one recovery signal and stayed failed when it never came. Each panel
// now reads again by itself while it is failed; the signals only make it faster. And one banner says the server is
// unreachable while the recovery loop runs.
registerAppMessages(i18n)

let visibility: DocumentVisibilityState = 'visible'
const scopes: Array<ReturnType<typeof effectScope>> = []

beforeEach(() => {
  vi.useFakeTimers()
  visibility = 'visible'
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
})

afterEach(() => {
  scopes.splice(0).forEach((scope) => scope.stop())
  setConnectionLost(false)
  delete (document as unknown as Record<string, unknown>).visibilityState
  vi.restoreAllMocks()
  vi.useRealTimers()
})

/** A panel whose reads fail `failures` times and then succeed; each read takes `ms`. */
function panel(failures: number, ms = 100) {
  const failed = ref(false)
  const busy = ref(false)
  let left = failures
  const reload = vi.fn(() => {
    busy.value = true
    setTimeout(() => {
      busy.value = false
      failed.value = left-- > 0
    }, ms)
  })
  const scope = effectScope()
  scopes.push(scope)
  const heal = scope.run(() => useSelfHeal({ failed: () => failed.value, busy: () => busy.value, reload }))!
  return { failed, busy, reload, heal, scope }
}

describe('0060 useSelfHeal', () => {
  it('reads a failed panel again after 5 s, 10 s, 20 s and then every 30 s, and stops once it is healthy', async () => {
    const p = panel(4)
    p.failed.value = true
    await vi.advanceTimersByTimeAsync(SELF_HEAL_DELAYS_MS[0] - 1)
    expect(p.reload).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(p.reload).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(100 + 10000)
    expect(p.reload).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(100 + 20000)
    expect(p.reload).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(100 + 30000)
    expect(p.reload).toHaveBeenCalledTimes(4)
    await vi.advanceTimersByTimeAsync(100 + 30000)
    expect(p.reload).toHaveBeenCalledTimes(5)
    // The fifth read succeeds: nothing more.
    await vi.advanceTimersByTimeAsync(100)
    expect(p.failed.value).toBe(false)
    await vi.advanceTimersByTimeAsync(10 * 60_000)
    expect(p.reload).toHaveBeenCalledTimes(5)
  })

  it('does nothing while the panel is healthy, and starts over at 5 s after the next failure', async () => {
    const p = panel(0)
    await vi.advanceTimersByTimeAsync(10 * 60_000)
    expect(p.reload).not.toHaveBeenCalled()
    p.failed.value = true
    await vi.advanceTimersByTimeAsync(5000)
    expect(p.reload).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(100)
    expect(p.failed.value).toBe(false)
    p.failed.value = true
    await vi.advanceTimersByTimeAsync(4999)
    expect(p.reload).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(p.reload).toHaveBeenCalledTimes(2)
  })

  it('waits for a read in flight instead of sending another', async () => {
    const p = panel(0, 60_000)
    p.failed.value = true
    p.busy.value = true
    await vi.advanceTimersByTimeAsync(30_000)
    expect(p.reload).not.toHaveBeenCalled()
    p.busy.value = false
    await vi.advanceTimersByTimeAsync(5000)
    expect(p.reload).toHaveBeenCalledTimes(1)
  })

  it('reads at once when the connection is back (fg:connection_recovered)', async () => {
    const p = panel(5)
    p.failed.value = true
    window.dispatchEvent(new Event('fg:connection_recovered'))
    expect(p.reload).toHaveBeenCalledTimes(1)
    // A signal during the read changes nothing.
    window.dispatchEvent(new Event('fg:connection_recovered'))
    expect(p.reload).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(100)
    window.dispatchEvent(new Event('fg:connection_recovered'))
    expect(p.reload).toHaveBeenCalledTimes(2)
    // 'online' is the panels' own signal (one signal, one read).
    await vi.advanceTimersByTimeAsync(100)
    window.dispatchEvent(new Event('online'))
    expect(p.reload).toHaveBeenCalledTimes(2)
  })

  it('a hidden page waits and reads as soon as it is shown again', async () => {
    const p = panel(5)
    visibility = 'hidden'
    p.failed.value = true
    await vi.advanceTimersByTimeAsync(5 * 60_000)
    expect(p.reload).not.toHaveBeenCalled()
    visibility = 'visible'
    document.dispatchEvent(new Event('visibilitychange'))
    expect(p.reload).toHaveBeenCalledTimes(1)
  })

  it('a reload that fails at once (never busy) is still followed by the next one', async () => {
    const failed = ref(true)
    const reload = vi.fn()
    const scope = effectScope()
    scopes.push(scope)
    scope.run(() => useSelfHeal({ failed: () => failed.value, busy: () => false, reload }))
    await vi.advanceTimersByTimeAsync(5000)
    expect(reload).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(10000)
    expect(reload).toHaveBeenCalledTimes(2)
  })

  it('stops with its scope', async () => {
    const p = panel(5)
    p.failed.value = true
    p.scope.stop()
    window.dispatchEvent(new Event('fg:connection_recovered'))
    await vi.advanceTimersByTimeAsync(5 * 60_000)
    expect(p.reload).not.toHaveBeenCalled()
  })
})

describe('0060 connection state for the one banner', () => {
  it('createConnectionRecovery reports its loop starting and ending', async () => {
    const onActive = vi.fn()
    const recovered = vi.fn()
    const probe = vi.fn().mockRejectedValueOnce(new Error('down')).mockResolvedValue({})
    const loop = createConnectionRecovery(probe, recovered, onActive)
    loop.start()
    loop.start()
    expect(onActive.mock.calls).toEqual([[true]])
    await vi.advanceTimersByTimeAsync(5000 + 10000)
    expect(recovered).toHaveBeenCalledTimes(1)
    expect(onActive.mock.calls).toEqual([[true], [false]])
    loop.stop()
    expect(onActive).toHaveBeenCalledTimes(2)
  })

  it('a GET that fails after its retries sets connectionLost; the next successful GET clears it', async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    vi.spyOn(axios, 'get').mockRejectedValue(new Error('still down'))
    const original = api.defaults.adapter
    try {
      api.defaults.adapter = async (config) => {
        throw new AxiosError('failed', 'ERR_NETWORK', config)
      }
      const failing = api.get('/list').catch((error) => error)
      await vi.advanceTimersByTimeAsync(9500)
      await failing
      expect(connectionLost.value).toBe(true)
      api.defaults.adapter = async (config) => ({ data: {}, status: 200, statusText: 'OK', headers: {}, config })
      await api.get('/list')
      expect(connectionLost.value).toBe(false)
    } finally {
      api.defaults.adapter = original
    }
  })

  it('the banner shows only while the loss lasts beyond a blip, and goes by itself', async () => {
    i18n.global.locale.value = 'ko'
    const wrapper = mount(ConnectionBanner, { global: { plugins: [i18n] } })
    try {
      setConnectionLost(true)
      await nextTick()
      expect(wrapper.find('[data-test="connection-banner"]').exists()).toBe(false)
      await vi.advanceTimersByTimeAsync(1500)
      expect(wrapper.get('[data-test="connection-banner"]').text()).toContain('자동으로 다시 연결하는 중')
      setConnectionLost(false)
      await nextTick()
      await vi.advanceTimersByTimeAsync(300)
      expect(wrapper.find('[data-test="connection-banner"]').exists()).toBe(false)
      // A blip shorter than the delay never shows it.
      setConnectionLost(true)
      await vi.advanceTimersByTimeAsync(1000)
      setConnectionLost(false)
      await vi.advanceTimersByTimeAsync(2000)
      expect(wrapper.find('[data-test="connection-banner"]').exists()).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })
})
