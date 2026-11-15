import { afterAll, describe, expect, it } from 'vitest'
import type { InternalAxiosRequestConfig } from 'axios'
import api from '@shared/api'
const original = api.defaults.adapter
let seen: InternalAxiosRequestConfig | null = null
api.defaults.adapter = async config => {
  seen = config
  return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
}
afterAll(() => { api.defaults.adapter = original })
describe('0149 only exact start POST receives 130 seconds', () => {
  it.each([
    ['post', '/api/v1/flows/f/runs', 130_000],
    ['POST', '/api/v1/flows/f/runs?mode=step', 130_000],
    ['get', '/api/v1/flows/f/runs', 30_000],
    ['get', '/api/v1/flows/f/runs/current', 30_000],
    ['get', '/api/v1/flows/f/runs/commands/cid', 30_000],
    ['post', '/api/v1/flows/f/runs/commands/cid/void', 30_000],
    ['post', '/api/v1/runs/r/pause', 30_000],
    ['post', '/api/v1/runs/r/resume', 30_000],
    ['post', '/api/v1/runs/r/stop', 30_000],
    ['post', '/api/v1/flows/f/runs/child', 30_000],
    ['post', '/prefix/api/v1/flows/f/runs', 30_000],
  ])('%s %s => %i', async (method, url, timeout) => {
    await api.request({ method, url, data: {} })
    expect(seen?.timeout).toBe(timeout)
  })
  it('respects a caller timeout', async () => {
    await api.post('/api/v1/flows/f/runs', {}, { timeout: 1000 })
    expect(seen?.timeout).toBe(1000)
  })
})
