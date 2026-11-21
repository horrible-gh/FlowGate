import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as ops from '@/features/designer/flowAttachments/operations'
import { setCurrentUser } from '@/core/auth/currentUser'
import { UNDO_MS } from '@/features/designer/flowAttachments/undo'
import type { FlowFile } from '@/features/designer/flowAttachments/api'

// (R3/R4/R5): failures kept with the Flow, the rule for when they go away, the delete
// group's own result and the one source of the ring time. Fake timers: five seconds are the undo time.

const api = vi.hoisted(() => ({ readFiles: vi.fn(), deleteFile: vi.fn(), uploadFile: vi.fn(), downloadFile: vi.fn(), downloadZip: vi.fn() }))
vi.mock('@/features/designer/flowAttachments/api', () => api)
vi.mock('@shared/api', () => ({ getRequest: vi.fn(), serverLogout: vi.fn() }))

const file = (key: string): FlowFile => ({
  key, filename: `${key}.txt`, display_name: `${key}.txt`, doc_id: 'doc', size: 1, content_sha256: `sha-${key}`, source_type: 'R', source_title: 'Req',
})
const A = { projectId: 'p', flowId: 'A' } as const
const server = (message: string) => ({ response: { data: { error: { message } } } })
const deferred = () => {
  let resolve!: (value?: unknown) => void, reject!: (error: unknown) => void
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
const incoming = (n: number) => Array.from({ length: n }, (_, i) => new File([`${i}`], `${i}.txt`))
const fiveSeconds = async () => { await vi.advanceTimersByTimeAsync(UNDO_MS); await flushPromises() }

beforeEach(() => {
  vi.clearAllMocks()
  setCurrentUser({ username: 'kim', email: null, isAdmin: false })
  api.readFiles.mockResolvedValue([file('a'), file('b'), file('c')])
  api.deleteFile.mockResolvedValue({})
  api.uploadFile.mockReset().mockResolvedValue({})
  vi.useFakeTimers()
})
afterEach(() => {
  ops.disposeFlowAttachmentOperations()
  setCurrentUser(null)
  vi.useRealTimers()
})

describe('R3 — a failure while the window is closed', () => {
  it('a closed delete failure is kept with the Flow, the file is in the list again and the list is marked for reading', async () => {
    const state = ops.getFlowState(A)
    state.files = [file('a'), file('b')]
    api.deleteFile.mockRejectedValueOnce(server('Locked.'))
    ops.stageDelete(A, [file('a')])
    await fiveSeconds()
    expect(state.errors).toMatchObject([{ kind: 'delete', targets: ['a.txt'], text: 'Locked.' }])
    expect(state.errors[0]).toEqual(expect.objectContaining({ id: expect.any(Number), operation: expect.any(Number), order: expect.any(Number) }))
    expect(state.deleting.size).toBe(0)
    expect(state.files.map(f => f.key)).toEqual(['a', 'b'])
    expect(state.dirty).toBe(true)
    expect(api.readFiles).not.toHaveBeenCalled() // closed: nothing is read until a window opens
  })

  it('a delete failure brings back a file a list read had dropped, from the snapshot taken at 🗑', async () => {
    const state = ops.getFlowState(A)
    state.files = [file('b')]
    api.deleteFile.mockRejectedValueOnce(server('Locked.'))
    ops.stageDelete(A, [file('a')])
    await fiveSeconds()
    expect(state.files.map(f => f.key).sort()).toEqual(['a', 'b'])
  })

  it('the failure survives closing, reopening and a list read that succeeds (they clear nothing)', async () => {
    api.deleteFile.mockRejectedValueOnce(server('Locked.'))
    ops.attachView(A, 'w1')
    ops.stageDelete(A, [file('a')])
    await fiveSeconds()
    ops.detachView(A, 'w1')
    ops.attachView(A, 'w2'); await ops.refresh(A); await ops.refresh(A)
    ops.detachView(A, 'w2'); ops.attachView(A, 'w3')
    expect(ops.getFlowState(A).errors).toHaveLength(1)
  })

  it('a closed upload failure keeps the failed name; the remaining files still upload', async () => {
    api.uploadFile.mockRejectedValueOnce(server('Too big.'))
    ops.enqueueUpload(A, incoming(3)); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(3)
    expect(ops.getFlowState(A).errors).toMatchObject([{ kind: 'upload', targets: ['0.txt'], text: 'Too big.' }])
  })

  it('a failure without a server message is stored as null, so the reopened window writes its own generic text', async () => {
    api.deleteFile.mockRejectedValueOnce(new Error('offline'))
    ops.stageDelete(A, [file('a')]); await fiveSeconds()
    expect(ops.getFlowState(A).errors[0].text).toBeNull()
  })

  it('files that failed for different reasons are separate lines; the same reason is one line with both names', async () => {
    api.deleteFile.mockRejectedValueOnce(server('Locked.')).mockRejectedValueOnce(server('Locked.')).mockRejectedValueOnce(server('Gone.'))
    ops.stageDelete(A, [file('a'), file('b'), file('c')]); await fiveSeconds()
    expect(ops.getFlowState(A).errors.map(e => [e.targets, e.text])).toEqual([[['a.txt', 'b.txt'], 'Locked.'], [['c.txt'], 'Gone.']])
  })

  it('the cancelled-because-the-window-closed note is cleared by the close button path', async () => {
    api.uploadFile.mockRejectedValueOnce({ response: { data: { error: { code: 'ATTACHMENT_NAME_CONFLICT', details: { key: 'k', content_sha256: 'h' } } } } })
    ops.enqueueUpload(A, incoming(2)); await flushPromises()
    const state = ops.getFlowState(A)
    expect(state.cancelledUploads).toEqual(['0.txt'])
    expect(ops.clearCancelled(A)).toBe(true)
    expect(state.cancelledUploads).toEqual([])
    expect(ops.clearCancelled(A)).toBe(false)
  })
})

describe('R3 — when a failure goes away', () => {
  const fail = (text = 'x') => { ops.actionFailed(A, 'download', server(text), ['x.txt']); return ops.getFlowState(A).errors.at(-1)! }

  it('the close button removes that line only; an id that is not there is refused', () => {
    const first = fail('first'), second = fail('second')
    ops.actionFailed(A, 'download', server('other'), ['y.txt'])
    expect(ops.clearError(A, first.id)).toBe(true)
    expect(ops.getFlowState(A).errors.map(e => e.text)).toEqual(['second', 'other'])
    expect(ops.clearError(A, first.id)).toBe(false)
    expect(second.id).not.toBe(first.id)
  })

  it('the next successful delete clears a failure from before it was pressed', async () => {
    fail()
    ops.stageDelete(A, [file('a')])
    await fiveSeconds()
    expect(ops.getFlowState(A).errors).toEqual([])
  })

  it('the next successful upload batch clears an older failure', async () => {
    fail()
    ops.enqueueUpload(A, incoming(2)); await flushPromises()
    expect(ops.getFlowState(A).errors).toEqual([])
  })

  it('a successful download clears a failure from before it was pressed (beginAction / actionSucceeded)', () => {
    fail()
    const started = ops.beginAction()
    ops.actionSucceeded(A, started)
    expect(ops.getFlowState(A).errors).toEqual([])
  })

  it('a success that finishes late does not clear a failure that happened after it started', async () => {
    const slow = deferred()
    api.uploadFile.mockReturnValueOnce(slow.promise)
    ops.enqueueUpload(A, incoming(1)); await flushPromises() // started first, still running
    const state = ops.getFlowState(A)
    api.deleteFile.mockRejectedValueOnce(server('Locked.'))
    ops.stageDelete(A, [file('a')]); await fiveSeconds() // started later, failed
    expect(state.errors).toMatchObject([{ kind: 'delete' }])
    slow.resolve(); await flushPromises() // the earlier upload succeeds now
    expect(state.errors).toMatchObject([{ kind: 'delete', text: 'Locked.' }])
  })

  it('a delete pressed before a failure does not clear it when it succeeds after the failure', async () => {
    ops.stageDelete(A, [file('a')])
    await vi.advanceTimersByTimeAsync(2000)
    fail() // happens while the five seconds of the pressed delete are still counting
    await fiveSeconds()
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
    expect(ops.getFlowState(A).errors).toHaveLength(1)
  })

  it('a group that failed in part keeps its own failure and clears nothing; the part that worked does not hide it', async () => {
    fail()
    api.deleteFile.mockResolvedValueOnce({}).mockRejectedValueOnce(server('Locked.'))
    ops.stageDelete(A, [file('a'), file('b')]); await fiveSeconds()
    expect(ops.getFlowState(A).errors.map(e => e.text)).toEqual(['x', 'Locked.'])
  })

  it('a group that all worked clears the failure of an earlier group (R4)', async () => {
    api.deleteFile.mockRejectedValueOnce(server('Locked.'))
    ops.stageDelete(A, [file('a')]); await fiveSeconds()
    expect(ops.getFlowState(A).errors).toHaveLength(1)
    ops.stageDelete(A, [file('b'), file('c')]); await fiveSeconds()
    expect(ops.getFlowState(A).errors).toEqual([])
  })

  it('an upload batch that failed in part keeps its failure; a batch that all worked clears older ones', async () => {
    api.uploadFile.mockResolvedValueOnce({}).mockRejectedValueOnce(server('Too big.')).mockResolvedValueOnce({})
    ops.enqueueUpload(A, incoming(3)); await flushPromises()
    expect(ops.getFlowState(A).errors).toMatchObject([{ kind: 'upload', targets: ['1.txt'] }])
    ops.enqueueUpload(A, incoming(2)); await flushPromises()
    expect(ops.getFlowState(A).errors).toEqual([])
  })

  it('a later success clears the cancelled-files note recorded before it, not one of its own batch', async () => {
    api.uploadFile.mockRejectedValueOnce({ response: { data: { error: { code: 'ATTACHMENT_NAME_CONFLICT', details: { key: 'k', content_sha256: 'h' } } } } })
    ops.enqueueUpload(A, incoming(2)); await flushPromises()
    expect(ops.getFlowState(A).cancelledUploads).toEqual(['0.txt']) // its own batch finished fine and kept the note
    ops.enqueueUpload(A, incoming(1)); await flushPromises()
    expect(ops.getFlowState(A).cancelledUploads).toEqual([])
  })

  it('a read-only lock or a session end is not a success: nothing is cleared', async () => {
    fail()
    ops.stageDelete(A, [file('a')])
    ops.setReadOnly(A, true); await fiveSeconds()
    expect(api.deleteFile).not.toHaveBeenCalled()
    expect(ops.getFlowState(A).errors).toHaveLength(1)
  })

  it('never discards a kept failure by itself: every one of many failures stays, and the same failure is not repeated', () => {
    for (let i = 0; i < 7; i++) ops.actionFailed(A, 'download', server(`m${i}`), [`f${i}.txt`])
    ops.actionFailed(A, 'download', server('m6'), ['f6.txt'])
    expect(ops.getFlowState(A).errors.map(e => e.text)).toEqual(['m0', 'm1', 'm2', 'm3', 'm4', 'm5', 'm6'])
  })

  it('six upload failures with the window closed all stay after closing and reopening; only the close button or a later success removes them', async () => {
    api.uploadFile.mockReset().mockImplementation(async (_scope: unknown, f: File) => { throw server(`No ${f.name}.`) })
    ops.attachView(A, 'w1'); ops.detachView(A, 'w1') // the window was open once and is now closed
    ops.enqueueUpload(A, incoming(6)); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(6)
    const state = ops.getFlowState(A)
    const expected = ['0.txt', '1.txt', '2.txt', '3.txt', '4.txt', '5.txt']
    expect(state.errors.map(e => e.targets[0]).sort()).toEqual(expected)
    ops.attachView(A, 'w2'); await ops.refresh(A); ops.detachView(A, 'w2'); ops.attachView(A, 'w3')
    expect(state.errors.map(e => e.targets[0]).sort()).toEqual(expected) // reopening and reading the list clear nothing
    expect(ops.clearError(A, state.errors[0].id)).toBe(true) // the close button removes that line only
    expect(state.errors).toHaveLength(5)
    api.uploadFile.mockReset().mockResolvedValue({})
    ops.enqueueUpload(A, incoming(1)); await flushPromises() // a later successful action clears the older failures
    expect(state.errors).toEqual([])
  })

  it('six delete failures with the window closed all stay and every file is back in the list', async () => {
    const six = ['a', 'b', 'c', 'd', 'e', 'f'].map(file)
    ops.getFlowState(A).files = six
    api.deleteFile.mockImplementation(async (_scope: unknown, f: { key: string }) => { throw server(`Locked ${f.key}.`) })
    ops.stageDelete(A, six); await fiveSeconds()
    const state = ops.getFlowState(A)
    expect(state.errors.map(e => e.text).sort()).toEqual(six.map(f => `Locked ${f.key}.`))
    expect(state.files.map(f => f.key).sort()).toEqual(['a', 'b', 'c', 'd', 'e', 'f'])
  })
})

describe('R3 — a failed list read is apart from a failed work', () => {
  it('does not replace a delete failure; a successful read clears only itself', async () => {
    ops.attachView(A, 'w')
    api.deleteFile.mockRejectedValueOnce(server('Locked.'))
    ops.stageDelete(A, [file('a')])
    api.readFiles.mockRejectedValue(server('Cannot read.'))
    await fiveSeconds()
    const state = ops.getFlowState(A)
    expect(state.errors).toMatchObject([{ kind: 'delete', text: 'Locked.' }])
    expect(state.readError).toMatchObject({ kind: 'read', text: 'Cannot read.' })
    api.readFiles.mockResolvedValue([file('a')])
    await ops.refresh(A)
    expect(state.readError).toBeNull()
    expect(state.errors).toHaveLength(1)
  })

  it('a failure of the approval-request read of the window is the same read error; the close button removes it', () => {
    ops.readFailed(A, server('No approvals.'))
    const state = ops.getFlowState(A)
    expect(state.readError).toMatchObject({ kind: 'read', text: 'No approvals.' })
    expect(ops.clearError(A, state.readError!.id)).toBe(true)
    expect(state.readError).toBeNull()
  })
})

describe('R4 — the group decides, and the list is read only after a failure', () => {
  it('an old failure is not made new by a later success: no list read when the group worked', async () => {
    ops.attachView(A, 'w')
    ops.actionFailed(A, 'download', server('x'), ['x.txt'])
    ops.stageDelete(A, [file('a')]); await fiveSeconds()
    expect(api.readFiles).not.toHaveBeenCalled()
    expect(ops.getFlowState(A).errors).toEqual([])
  })

  it('a failed group reads the list once while a window shows it', async () => {
    ops.attachView(A, 'w')
    api.deleteFile.mockRejectedValueOnce(server('Locked.')).mockRejectedValueOnce(server('Locked.'))
    ops.stageDelete(A, [file('a'), file('b')]); await fiveSeconds()
    expect(api.readFiles).toHaveBeenCalledTimes(1)
  })

  it('a failure of the delete does not hide the next group: it is judged on its own', async () => {
    ops.attachView(A, 'w')
    api.deleteFile.mockRejectedValueOnce(server('Locked.'))
    ops.stageDelete(A, [file('a')]); await fiveSeconds()
    api.readFiles.mockClear()
    ops.stageDelete(A, [file('b')]); await fiveSeconds()
    expect(api.readFiles).not.toHaveBeenCalled()
  })
})

describe('R5 — one number for the undo time', () => {
  const window = readFileSync(resolve(__dirname, '../../../src/features/designer/flowAttachments/FlowAttachmentWindow.vue'), 'utf8')
  it('the ring animation takes its time from --fa-undo-ms, set from UNDO_MS', () => {
    expect(window).toMatch(/animation:\s*fa-ringdown var\(--fa-undo-ms\)/)
    expect(window).toMatch(/'--fa-undo-ms':\s*`\$\{UNDO_MS\}ms`/)
    expect(window).not.toMatch(/\b5000ms\b/)
  })
  it('keeps the negative animation delay that continues the ring after a reopen', () => {
    expect(window).toMatch(/animationDelay: `-\$\{Math\.min\(UNDO_MS/)
  })
})
