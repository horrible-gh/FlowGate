import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { setCurrentUser } from '@/core/auth/currentUser'
import { announceAuthSessionEnded } from '@shared/authSessionSignal'
import * as ops from '@/features/designer/flowAttachments/operations'
import { pagehideDeleteUrl } from '@/features/designer/flowAttachments/pagehide'
import type { FlowFile } from '@/features/designer/flowAttachments/api'

const mocks = vi.hoisted(() => ({ base: '/flowgate', readFiles: vi.fn(), deleteFile: vi.fn() }))
vi.mock('@shared/api', () => ({ getBaseUrl: () => mocks.base, getRequest: vi.fn() }))
vi.mock('@/features/designer/flowAttachments/api', () => mocks)
const A = { projectId: 'p', flowId: 'flow /한글' }
const B = { projectId: 'q', flowId: 'B' }
const file = (key: string): FlowFile => ({ key, filename: key, display_name: key, doc_id: 'd', size: 1,
  content_sha256: 'hash +/&=', source_type: 'R', source_title: 'Req' })
const fetcher = vi.fn()
const archived = vi.fn(async (_id: string) => false)
const subscribe = vi.fn(() => () => {})
function start() { return ops.startFlowAttachmentOperations({ subscribe, readArchived: archived }) }
function hide() { window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })) }
function show(persisted = true) { window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted })) }
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}
beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  mocks.base = '/flowgate'
  mocks.readFiles.mockResolvedValue([file('a'), file('b')])
  mocks.deleteFile.mockResolvedValue({})
  archived.mockResolvedValue(false)
  fetcher.mockReset().mockResolvedValue({ ok: true })
  vi.stubGlobal('fetch', fetcher)
  setCurrentUser({ username: 'kim', email: null, isAdmin: false })
  window.__accessToken__ = 'initial-token'
  start()
})
afterEach(() => {
  ops.disposeFlowAttachmentOperations()
  setCurrentUser(null)
  delete window.__accessToken__
  sessionStorage.removeItem('fg_logout_in_progress')
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('pagehide deletes (R1 F5)', () => {
  it('drains every group and scope synchronously, using the current token and snapshot hash without a body', async () => {
    const wait = deferred<{ ok: boolean }>()
    fetcher.mockReturnValue(wait.promise)
    ops.stageDelete(A, [file('a /한글'), file('b')])
    ops.stageDelete(B, [file('c')])
    window.__accessToken__ = 'refreshed-token'
    hide()
    expect(fetcher).toHaveBeenCalledTimes(3) // before any request resolves
    expect(ops.pendingFiles(A)).toEqual([])
    expect(ops.getFlowState(A).deleting.size).toBe(2)
    const [url, init] = fetcher.mock.calls[0]
    expect(url).toBe(`${window.location.origin}/flowgate/api/v1/flows/flow%20%2F%ED%95%9C%EA%B8%80/attachments/a%20%2F%ED%95%9C%EA%B8%80?expected_sha256=hash%20%2B%2F%26%3D`)
    expect(init).toEqual({ method: 'DELETE', keepalive: true, headers: { Authorization: 'Bearer refreshed-token' } })
    expect(init).not.toHaveProperty('body')
    hide()
    await vi.advanceTimersByTimeAsync(6000)
    expect(fetcher).toHaveBeenCalledTimes(3)
    expect(mocks.deleteFile).not.toHaveBeenCalled()
    wait.resolve({ ok: true })
    await flushPromises()
    expect(ops.getFlowState(A).deleting.size).toBe(0)
  })

  it.each(['/flowgate///', 'https://api.example.test//flowgate//'])('normalizes configured base %s', base => {
    mocks.base = base
    const url = new URL(pagehideDeleteUrl('f', file('x')))
    expect(url.origin).toBe(base.startsWith('https') ? 'https://api.example.test' : window.location.origin)
    expect(url.pathname).toBe('/flowgate/api/v1/flows/f/attachments/x')
    expect(url.searchParams.get('expected_sha256')).toBe(file('x').content_sha256)
  })

  it('does not resend a timer-owned group or its sequential tail', async () => {
    const wait = deferred<unknown>()
    mocks.deleteFile.mockReturnValueOnce(wait.promise)
    ops.stageDelete(A, [file('a'), file('b')])
    await vi.advanceTimersByTimeAsync(5000)
    expect(mocks.deleteFile).toHaveBeenCalledTimes(1)
    hide()
    expect(fetcher).not.toHaveBeenCalled()
    wait.resolve({})
    await flushPromises()
    expect(mocks.deleteFile).toHaveBeenCalledTimes(2)
  })

  it('pagehide at the timer boundary claims the group only once', async () => {
    ops.stageDelete(A, [file('a')])
    await vi.advanceTimersByTimeAsync(4999)
    hide()
    await vi.advanceTimersByTimeAsync(1)
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(mocks.deleteFile).not.toHaveBeenCalled()
  })

  it.each(['token missing', 'logout', 'logout flag', 'different user', 'no user', 'read only'])('does not send when %s', reason => {
    ops.stageDelete(A, [file('a')])
    if (reason === 'token missing') delete window.__accessToken__
    if (reason === 'logout flag') sessionStorage.setItem('fg_logout_in_progress', '1')
    if (reason === 'logout') { announceAuthSessionEnded(); ops.stageDelete(A, [file('after-logout')]) }
    if (reason === 'different user') setCurrentUser({ username: 'lee', email: null, isAdmin: false })
    if (reason === 'no user') setCurrentUser(null)
    if (reason === 'read only') ops.setReadOnly(A, true)
    hide()
    expect(fetcher).not.toHaveBeenCalled()
    expect(ops.pendingFiles(A)).toEqual([])
  })

  it('undo removes a file from the pagehide snapshot', () => {
    ops.stageDelete(A, [file('a'), file('b')])
    ops.undoItem(A, 'a')
    hide()
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher.mock.calls[0][0]).toContain('/attachments/b?')
  })

  it('bfcache restore rereads list and permissions without rescheduling a drained group', async () => {
    const wait = deferred<{ ok: boolean }>()
    fetcher.mockReturnValue(wait.promise)
    ops.stageDelete(A, [file('a')])
    hide(); show()
    expect(mocks.readFiles).toHaveBeenCalledWith(A.flowId)
    expect(archived).toHaveBeenCalledWith(A.flowId)
    await flushPromises()
    wait.resolve({ ok: true })
    await flushPromises()
    expect(ops.getFlowState(A).files.map(f => f.key)).toEqual(['b'])
    const revision = ops.getFlowState(A).revision
    hide(); show()
    await vi.advanceTimersByTimeAsync(6000)
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(mocks.deleteFile).not.toHaveBeenCalled()
    expect(ops.getFlowState(A).revision).toBe(revision)
    expect(ops.pendingFiles(A)).toEqual([])
  })

  it('restore applies read-only permission even after the queue has drained', async () => {
    ops.stageDelete(A, [file('a')]); hide()
    await flushPromises()
    archived.mockResolvedValue(true)
    show(); await flushPromises()
    expect(ops.getFlowState(A).readOnly).toBe(true)
  })

  it.each([401, 403, 404, 409])('keeps failure and restores the row for HTTP %s', async status => {
    const state = ops.getFlowState(A)
    state.files = [file('a')]
    fetcher.mockResolvedValue({ ok: false, status, json: async () => ({ error: { message: `error-${status}` } }) })
    ops.stageDelete(A, [file('a')]); hide()
    await flushPromises()
    expect(state.error).toEqual({ text: `error-${status}` })
    expect(state.files).toHaveLength(1)
    expect(state.deleting.size).toBe(0)
    show(); await flushPromises()
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(state.error).toEqual({ text: `error-${status}` })
  })

  it.each(['reject', 'throw'])('records transport %s without preventing other immediate requests', async mode => {
    if (mode === 'throw') fetcher.mockImplementationOnce(() => { throw new Error('offline') })
    else fetcher.mockRejectedValueOnce(new Error('offline'))
    ops.stageDelete(A, [file('a'), file('b')]); hide()
    expect(fetcher).toHaveBeenCalledTimes(2)
    await flushPromises()
    expect(ops.getFlowState(A).error).toEqual({ text: null })
    expect(ops.getFlowState(A).deleting.size).toBe(0)
  })

  it('a late bfcache list response cannot resurrect a successfully deleted row', async () => {
    const deletion = deferred<{ ok: boolean }>()
    const listing = deferred<FlowFile[]>()
    fetcher.mockReturnValue(deletion.promise)
    mocks.readFiles.mockReturnValue(listing.promise)
    const state = ops.getFlowState(A)
    state.files = [file('a')]
    ops.stageDelete(A, [file('a')]); hide(); show()
    deletion.resolve({ ok: true }); await flushPromises()
    listing.resolve([file('a')]); await flushPromises()
    expect(state.files).toEqual([])
    expect(state.loading).toBe(false)
  })

  it('restores the failed file snapshot even when it is absent from the cached list', async () => {
    fetcher.mockRejectedValueOnce(new Error('offline'))
    ops.stageDelete(A, [file('a')]); hide(); await flushPromises()
    expect(ops.getFlowState(A).files.map(f => f.key)).toEqual(['a'])
  })

  it('ignores old generation results after logout', async () => {
    const wait = deferred<{ ok: boolean }>()
    fetcher.mockReturnValue(wait.promise)
    const old = ops.getFlowState(A)
    old.files = [file('a')]
    ops.stageDelete(A, [file('a')]); hide()
    announceAuthSessionEnded()
    const fresh = ops.getFlowState(A)
    fresh.files = [file('a')]
    wait.resolve({ ok: true }); await flushPromises()
    expect(fresh.files).toHaveLength(1)
    expect(old.files).toHaveLength(1)
  })

  it('installs once, disposes listeners, and never flushes on visibilitychange', async () => {
    const dispose = start(); start()
    ops.stageDelete(A, [file('a')])
    window.dispatchEvent(new Event('visibilitychange'))
    show(false)
    expect(fetcher).not.toHaveBeenCalled()
    expect(mocks.readFiles).not.toHaveBeenCalled()
    hide(); await flushPromises()
    expect(fetcher).toHaveBeenCalledTimes(1)
    dispose()
    start(); dispose()
    ops.stageDelete(A, [file('b')])
    hide(); show()
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(mocks.readFiles).not.toHaveBeenCalled()
  })
})
