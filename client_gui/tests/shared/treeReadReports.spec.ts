import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushTreeReports, readTreeReports, reportTreeFailure, type TreeReport } from '@shared/treeReadReports'
const row = (status: TreeReport['status'], id = 'tree-12345678'): TreeReport => ({ id, status, project_id: 'project', screen: 'full_tree', elapsed_ms: 10, occurred_at: new Date().toISOString(), request_id: 'request-123' })
beforeEach(() => { localStorage.clear() })
describe('tree failure reports', () => {
  it.each(['no_response', 504] as const)('keeps %s until success then removes it', async (status) => {
    const send = vi.fn().mockResolvedValue({})
    await reportTreeFailure('alice', row(status), send)
    expect(send).not.toHaveBeenCalled()
    expect(readTreeReports('bob')).toEqual([])
    await flushTreeReports('alice', send)
    expect(send).toHaveBeenCalledTimes(1)
    expect(readTreeReports('alice')).toEqual([])
  })
  it('sends 500 immediately', async () => {
    const send = vi.fn().mockResolvedValue({})
    const report = row(500)
    await reportTreeFailure('alice', report, send)
    expect(send).toHaveBeenCalledWith(report)
    expect(readTreeReports('alice')).toEqual([])
  })
  it('bounds storage and stores only the schema', async () => {
    const send = vi.fn()
    for (let i = 0; i < 55; i++) await reportTreeFailure('alice', { ...row(504, `tree-12345678:${i}`), url: '?token=secret', headers: { Authorization: 'Bearer secret' } } as TreeReport, send)
    expect(readTreeReports('alice')).toHaveLength(50)
    expect(JSON.stringify(readTreeReports('alice'))).not.toContain('secret')
    expect(readTreeReports('alice')[0]?.id).toBe('tree-12345678:5')
  })
  it('failed delivery stays queued without recursion', async () => {
    const send = vi.fn().mockRejectedValue(new Error('offline'))
    await reportTreeFailure('alice', row(504), send)
    await flushTreeReports('alice', send)
    expect(send).toHaveBeenCalledTimes(1)
    expect(readTreeReports('alice')).toHaveLength(1)
  })
})
