import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import axios, { AxiosError } from 'axios'
import i18n from '@shared/i18n'
import api from '@shared/api'
import { registerAppMessages } from '@/app/i18n'
import ConnectionBanner from '@/app/components/ConnectionBanner.vue'
import { HOLD_MAX_MS, PASS_EVERY_MS, createConnectionGate } from '@shared/connectionGate'
import { connectionLost, setConnectionLost } from '@shared/connectionStatus'

// 0060 (22:09): with the browser refusing the server's renewed certificate, the banner said "reconnecting…" for
// ever and every panel's polling kept failing (1150 console errors). Now the banner turns into "unknown error" with
// [새로 고침] after 20 s, and while the connection is lost the app's GETs wait for it instead of going out.
registerAppMessages(i18n)

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  setConnectionLost(false)
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('0060 createConnectionGate', () => {
  it('lets every request through while the connection is up', () => {
    const gate = createConnectionGate(() => false)
    expect(gate.admit()).toBe('send')
    expect(gate.waiting()).toBe(0)
  })

  it('holds requests while lost and sends them all when the connection is back', async () => {
    let lost = true
    const gate = createConnectionGate(() => lost)
    gate.lost()
    const a = gate.admit() as Promise<string>
    const b = gate.admit() as Promise<string>
    expect(gate.waiting()).toBe(2)
    lost = false
    gate.open()
    await expect(a).resolves.toBe('send')
    await expect(b).resolves.toBe('send')
    expect(gate.waiting()).toBe(0)
  })

  it('fails a request held for HOLD_MAX_MS, and sends one for real once every PASS_EVERY_MS', async () => {
    const gate = createConnectionGate(() => true)
    gate.lost()
    const verdicts: string[] = []
    const ask = () => void (gate.admit() as Promise<string>).then((v) => verdicts.push(v))
    ask()
    await vi.advanceTimersByTimeAsync(HOLD_MAX_MS)
    expect(verdicts).toEqual(['fail'])
    // Still within PASS_EVERY_MS of the loss.
    await vi.advanceTimersByTimeAsync(PASS_EVERY_MS - 2 * HOLD_MAX_MS)
    ask()
    await vi.advanceTimersByTimeAsync(HOLD_MAX_MS - 1)
    expect(verdicts).toEqual(['fail'])
    await vi.advanceTimersByTimeAsync(1)
    expect(verdicts).toEqual(['fail', 'send'])
    // The next one waits for the next PASS_EVERY_MS again.
    ask()
    await vi.advanceTimersByTimeAsync(HOLD_MAX_MS)
    expect(verdicts).toEqual(['fail', 'send', 'fail'])
  })

  it('lets a cancelled request go on at once (axios rejects it as cancelled)', async () => {
    const gate = createConnectionGate(() => true)
    const controller = new AbortController()
    const held = gate.admit(controller.signal) as Promise<string>
    controller.abort()
    await expect(held).resolves.toBe('send')
    expect(gate.waiting()).toBe(0)
    expect(gate.admit(controller.signal)).toBe('send')
  })
})

describe('0060 api while the connection is lost', () => {
  const original = api.defaults.adapter

  afterEach(async () => {
    // End the module's recovery loop: its probe answers, and the held GET goes out.
    vi.spyOn(axios, 'get').mockResolvedValue({ data: {} })
    api.defaults.adapter = async (config) => ({ data: {}, status: 200, statusText: 'OK', headers: {}, config })
    const cleanup = api.get('/cleanup').catch(() => {})
    await vi.advanceTimersByTimeAsync(PASS_EVERY_MS)
    await cleanup
    api.defaults.adapter = original
  })

  async function loseConnection(adapter: ReturnType<typeof vi.fn>) {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    vi.spyOn(axios, 'get').mockRejectedValue(new Error('probe: still down'))
    adapter.mockImplementation(async (config) => {
      throw new AxiosError('Network Error', 'ERR_NETWORK', config)
    })
    api.defaults.adapter = adapter as never
    const first = api.get('/first').catch((error) => error)
    await vi.advanceTimersByTimeAsync(9500)
    await first
    expect(connectionLost.value).toBe(true)
    adapter.mockClear()
  }

  it('does not send GETs (nor re-send them) while lost; a held GET fails after HOLD_MAX_MS without being sent', async () => {
    const adapter = vi.fn()
    await loseConnection(adapter)
    const polls = [api.get('/notifications').catch((e) => e), api.get('/ai-invoke/active-all').catch((e) => e)]
    await vi.advanceTimersByTimeAsync(HOLD_MAX_MS)
    const errors = await Promise.all(polls)
    expect(adapter).not.toHaveBeenCalled()
    errors.forEach((error) => expect(error).toMatchObject({ code: 'ERR_NETWORK' }))
  })

  it('sends held GETs at once when the probe finds the connection back', async () => {
    const adapter = vi.fn()
    await loseConnection(adapter)
    const held = api.get('/list')
    await vi.advanceTimersByTimeAsync(1000)
    expect(adapter).not.toHaveBeenCalled()
    adapter.mockImplementation(async (config) => ({ data: { ok: 1 }, status: 200, statusText: 'OK', headers: {}, config }))
    vi.spyOn(axios, 'get').mockResolvedValue({ data: {} })
    // The probe runs 5 s after the loss.
    await vi.advanceTimersByTimeAsync(4000)
    await expect(held).resolves.toMatchObject({ data: { ok: 1 } })
    expect(adapter).toHaveBeenCalledTimes(1)
    expect(connectionLost.value).toBe(false)
  })

  it('once every PASS_EVERY_MS one GET goes out for real, and its success ends the loss', async () => {
    const adapter = vi.fn()
    await loseConnection(adapter)
    await vi.advanceTimersByTimeAsync(PASS_EVERY_MS - HOLD_MAX_MS + 500)
    adapter.mockImplementation(async (config) => ({ data: {}, status: 200, statusText: 'OK', headers: {}, config }))
    const passed = api.get('/list')
    await vi.advanceTimersByTimeAsync(HOLD_MAX_MS)
    await passed
    expect(adapter).toHaveBeenCalledTimes(1)
    expect(connectionLost.value).toBe(false)
  })

  it('never holds a write', async () => {
    const adapter = vi.fn()
    await loseConnection(adapter)
    adapter.mockImplementation(async (config) => ({ data: {}, status: 200, statusText: 'OK', headers: {}, config }))
    await api.post('/turn', {})
    expect(adapter).toHaveBeenCalledTimes(1)
  })
})

describe('0060 banner when the loss does not end', () => {
  it('turns into "unknown error" with a reload button after 20 s, and goes when the connection is back', async () => {
    i18n.global.locale.value = 'ko'
    const reload = vi.fn()
    const wrapper = mount(ConnectionBanner, { props: { reload }, global: { plugins: [i18n] } })
    try {
      setConnectionLost(true)
      await vi.advanceTimersByTimeAsync(1500)
      const banner = () => wrapper.get('[data-test="connection-banner"]')
      expect(banner().text()).toContain('자동으로 다시 연결하는 중')
      expect(wrapper.find('[data-test="connection-banner-reload"]').exists()).toBe(false)
      await vi.advanceTimersByTimeAsync(20_000 - 1500 - 1)
      expect(wrapper.find('[data-test="connection-banner-reload"]').exists()).toBe(false)
      await vi.advanceTimersByTimeAsync(1)
      expect(banner().text()).toContain('알 수 없는 오류가 발생했습니다')
      expect(banner().text()).not.toContain('인증서')
      expect(banner().attributes('role')).toBe('alert')
      await wrapper.get('[data-test="connection-banner-reload"]').trigger('click')
      expect(reload).toHaveBeenCalledTimes(1)
      setConnectionLost(false)
      await nextTick()
      await vi.advanceTimersByTimeAsync(300)
      expect(wrapper.find('[data-test="connection-banner"]').exists()).toBe(false)
      // The next loss starts over with "reconnecting".
      setConnectionLost(true)
      await vi.advanceTimersByTimeAsync(1500)
      expect(banner().text()).toContain('자동으로 다시 연결하는 중')
      expect(wrapper.find('[data-test="connection-banner-reload"]').exists()).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })
})
