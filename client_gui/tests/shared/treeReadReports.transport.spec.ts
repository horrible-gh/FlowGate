import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import axios, { AxiosError } from 'axios'
import api, { flushPendingTreeReports } from '@shared/api'
import { readTreeReports } from '@shared/treeReadReports'
const original = api.defaults.adapter
beforeEach(() => {
  localStorage.clear()
  window.__accessToken__ = `x.${btoa(JSON.stringify({ sub: 'alice' }))}.x`
})
afterEach(() => { api.defaults.adapter = original; vi.restoreAllMocks(); delete window.__accessToken__ })
it.each(['/api/v1/projects/p/groups/tree?token=secret', '/api/v1/projects/p/git/groups/g/tree'])('observes failure and later success for %s', async (path) => {
  const send = vi.spyOn(axios, 'post').mockResolvedValue({ data: { ok: true } })
  api.defaults.adapter = async (config) => {
    throw new AxiosError('secret response', 'ECONNABORTED', config)
  }
  await expect(api.get(path, { _getRetryCount: 99 } as never)).rejects.toBeDefined()
  expect(readTreeReports('alice')).toHaveLength(1)
  expect(JSON.stringify(readTreeReports('alice'))).not.toContain('secret')
  api.defaults.adapter = async (config) => ({ config, data: {}, status: 200, statusText: 'OK', headers: {} })
  await api.get(path)
  await vi.waitFor(() => expect(readTreeReports('alice')).toHaveLength(0))
  expect(send).toHaveBeenCalledTimes(1)
})
it('startup flush uses silent transport and retains a refused report', async () => {
  const send = vi.spyOn(axios, 'post').mockRejectedValue(new Error('401'))
  localStorage.setItem('flowgate.user.alice.tree-read-failures', JSON.stringify([{ id: 'tree-12345678', project_id: 'p', screen: 'full_tree', status: 504, elapsed_ms: 1, occurred_at: new Date().toISOString(), request_id: 'request-1' }]))
  await flushPendingTreeReports()
  expect(send).toHaveBeenCalledTimes(1)
  expect(readTreeReports('alice')).toHaveLength(1)
  expect(window.__accessToken__).toBeDefined()
})
