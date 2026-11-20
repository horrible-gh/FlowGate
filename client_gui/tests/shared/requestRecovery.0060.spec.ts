import { afterEach, describe, expect, it } from 'vitest'
import { effectScope, ref } from 'vue'
import { AxiosError } from 'axios'
import api from '@shared/api'
import { notifyGetRetry, useRequestRecovery, type RetryNotice } from '@shared/requestRecovery'

const originalAdapter = api.defaults.adapter
afterEach(() => { api.defaults.adapter = originalAdapter })

describe('0060 retry presentation', () => {
  it('notifies the waiting panel on a real GET retry but never replays a POST', async () => {
    const scope = effectScope()
    const loading = ref(true)
    const reconnecting = scope.run(() => useRequestRecovery(() => 'p:g', () => loading.value,
      notice => notice.path === '/api/v1/documents' && notice.groupId === 'g'))!
    let calls = 0
    api.defaults.adapter = async config => {
      calls++
      if (calls === 1 || config.method === 'post') {
        throw new AxiosError('unavailable', 'ERR_BAD_RESPONSE', config, undefined,
          { data: {}, status: 503, statusText: 'Unavailable', headers: {}, config })
      }
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
    }
    try {
      await api.get('/api/v1/documents', { params: { group_id: 'g' } })
      expect(calls).toBe(2)
      expect(reconnecting.value).toBe(true)
      loading.value = false
      expect(reconnecting.value).toBe(false)
      await expect(api.post('/api/v1/documents', {})).rejects.toBeInstanceOf(AxiosError)
      expect(calls).toBe(3)
    } finally { scope.stop() }
  })

  it('resets on scope changes and removes listeners on disposal', () => {
    const scope = effectScope()
    const key = ref('a')
    const reconnecting = scope.run(() => useRequestRecovery(() => key.value, () => true, () => true))!
    notifyGetRetry('/api/v1/documents')
    expect(reconnecting.value).toBe(true)
    key.value = 'b'
    expect(reconnecting.value).toBe(false)
    scope.stop()
    notifyGetRetry('/api/v1/documents')
    expect(reconnecting.value).toBe(false)
  })

  it('only publishes routing fields, without query secrets', () => {
    let notice: RetryNotice | undefined
    const listener = (event: Event) => { notice = (event as CustomEvent<RetryNotice>).detail }
    window.addEventListener('fg:get_retry', listener)
    try {
      notifyGetRetry('/api/v1/documents?token=private', { group_id: 'g', token: 'private' })
      expect(notice?.groupId).toBe('g')
      expect(JSON.stringify(notice)).not.toContain('private')
    } finally { window.removeEventListener('fg:get_retry', listener) }
  })
})
