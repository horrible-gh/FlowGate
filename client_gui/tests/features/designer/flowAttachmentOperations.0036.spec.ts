import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { nextTick, ref } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as ops from '@/features/designer/flowAttachments/operations'
import { setCurrentUser } from '@/core/auth/currentUser'
import { announceAuthSessionEnded, AUTH_SESSION_ENDED_EVENT } from '@shared/authSessionSignal'
import type { FlowFile } from '@/features/designer/flowAttachments/api'

// (R1): the Flow attachment work manager — pending deletes belong to the session, the
// Project and the Flow, not to a window. Fake timers: nothing before five seconds, exactly one request at five.

const order = vi.hoisted(() => [] as string[])
const api = vi.hoisted(() => ({ readFiles: vi.fn(), deleteFile: vi.fn(), uploadFile: vi.fn(), downloadFile: vi.fn(), downloadZip: vi.fn() }))
vi.mock('@/features/designer/flowAttachments/api', () => api)
vi.mock('@shared/api', () => ({
  getRequest: vi.fn(),
  serverLogout: vi.fn(async () => { order.push('serverLogout') }),
}))

const file = (key: string, sha = `sha-${key}`): FlowFile => ({
  key, filename: `${key}.txt`, display_name: `${key}.txt`, doc_id: 'doc', size: 1, content_sha256: sha, source_type: 'R', source_title: 'Req',
})
const A = { projectId: 'p', flowId: 'A' } as const
const B = { projectId: 'p', flowId: 'B' } as const
const A2 = { projectId: 'q', flowId: 'A' } as const

/** The archive watch's event subscription and archive read, faked: fire(project) is a server group_view_refresh. */
function archiveFake() {
  type Sub = { events: readonly string[]; event(): void; resync?(): void }
  const subs = new Map<string, Sub[]>()
  const unsubscribed: string[] = []
  const subscribe = vi.fn((projectId: string, sub: Sub) => {
    subs.set(projectId, [...(subs.get(projectId) ?? []), sub])
    return () => { subs.set(projectId, (subs.get(projectId) ?? []).filter(s => s !== sub)); unsubscribed.push(projectId) }
  })
  const readArchived = vi.fn(async (_flowId: string) => false)
  const fire = (projectId: string) => { for (const s of subs.get(projectId) ?? []) s.event() }
  const resync = (projectId: string) => { for (const s of subs.get(projectId) ?? []) s.resync?.() }
  const listening = (projectId: string) => (subs.get(projectId) ?? []).length
  return { subscribe, readArchived, fire, resync, listening, unsubscribed }
}

function deferred() {
  let resolve!: (value?: unknown) => void, reject!: (error: unknown) => void
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.clearAllMocks()
  order.length = 0
  setCurrentUser({ username: 'kim', email: null, isAdmin: false })
  api.readFiles.mockResolvedValue([file('a'), file('b')])
  api.deleteFile.mockResolvedValue({})
  vi.useFakeTimers()
})
afterEach(() => {
  ops.disposeFlowAttachmentOperations()
  setCurrentUser(null)
  vi.useRealTimers()
})

describe('flow attachment work manager — timing (R1 완료 기준 1·2)', () => {
  it('a staged delete with no window attached: nothing at 4999ms, exactly one request at 5000ms', async () => {
    const view = ops.attachView(A, 'w1')
    await ops.refresh(A)
    ops.stageDelete(A, [view.files[0]])
    ops.detachView(A, 'w1') // the window closes / unmounts
    await vi.advanceTimersByTimeAsync(4999); await flushPromises()
    expect(api.deleteFile).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1); await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
    expect(api.deleteFile).toHaveBeenCalledWith('A', expect.objectContaining({ key: 'a', content_sha256: 'sha-a' }))
    await vi.advanceTimersByTimeAsync(10000); await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
    expect(ops.getFlowState(A).files.map(f => f.key)).toEqual(['b'])
    expect(ops.getFlowState(A).deleting.size).toBe(0)
  })

  it('reattaching keeps the pending group, its start and the time left; undo after reopening sends nothing', async () => {
    ops.attachView(A, 'w1')
    ops.stageDelete(A, [file('a')])
    const start = ops.pendingGroup(A, 'a')!.start
    ops.detachView(A, 'w1')
    await vi.advanceTimersByTimeAsync(3000); await flushPromises()
    const state = ops.attachView(A, 'w2')
    expect(state).toBe(ops.getFlowState(A))
    expect(ops.pendingGroup(A, 'a')?.start).toBe(start)
    expect(Date.now() - start).toBe(3000) // the window's ringDelay(start) → -3000ms
    ops.undoItem(A, 'a')
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).not.toHaveBeenCalled()
  })

  it('the request uses the snapshot taken at 🗑, even if a later list read changed or dropped the file', async () => {
    ops.stageDelete(A, [file('a', 'old-hash')])
    api.readFiles.mockResolvedValue([file('b')])
    await ops.refresh(A)
    expect(ops.pendingFiles(A).map(f => f.key)).toEqual(['a'])
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledWith('A', expect.objectContaining({ key: 'a', content_sha256: 'old-hash' }))
  })

  it('a failed delete keeps the file, records the server reason and reads the list again while a window shows it', async () => {
    ops.attachView(A, 'w')
    await ops.refresh(A)
    api.deleteFile.mockRejectedValueOnce({ response: { data: { error: { message: 'The file changed.' } } } })
    ops.stageDelete(A, [file('a')])
    api.readFiles.mockClear()
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    const state = ops.getFlowState(A)
    expect(state.errors).toMatchObject([{ kind: 'delete', targets: ['a.txt'], text: 'The file changed.' }])
    expect(state.deleting.size).toBe(0)
    expect(state.files.map(f => f.key)).toEqual(['a', 'b'])
    expect(api.readFiles).toHaveBeenCalledTimes(1)
  })

  it('row ↶, the numbered ↶ (latest group of more than one) and Ctrl+Z (latest group) keep their meaning', async () => {
    ops.stageDelete(A, [file('a'), file('b')])
    ops.stageDelete(A, [file('c')])
    ops.stageDelete(A, [file('c')]) // never staged twice
    expect(ops.bulkGroup(A)?.items.map(i => i.key)).toEqual(['a', 'b'])
    expect(ops.undoLast(A)).toBe(true)
    expect(ops.pendingGroup(A, 'c')).toBeUndefined()
    ops.undoItem(A, 'a')
    expect(ops.pendingGroup(A, 'b')?.items.map(i => i.key)).toEqual(['b'])
    expect(ops.undoGroup(A, ops.pendingGroup(A, 'b'))).toBe(true)
    expect(ops.undoLast(A)).toBe(false)
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).not.toHaveBeenCalled()
  })
})

describe('flow attachment work manager — scopes', () => {
  it('Flows and Projects are kept apart: Ctrl+Z of one Flow never takes back another Flow', async () => {
    ops.stageDelete(A, [file('a')])
    ops.stageDelete(B, [file('b')])
    ops.stageDelete(A2, [file('x')])
    expect(ops.undoLast(B)).toBe(true)
    expect(ops.pendingGroup(A, 'a')).toBeDefined()
    expect(ops.pendingGroup(A2, 'x')).toBeDefined()
    expect(ops.getFlowState(A)).not.toBe(ops.getFlowState(A2))
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile.mock.calls.map(([flow, f]) => `${flow}:${(f as FlowFile).key}`).sort()).toEqual(['A:a', 'A:x'])
  })

  it('undo on a Flow with no state does not create one and answers false', () => {
    expect(ops.undoLast({ projectId: 'p', flowId: 'none' })).toBe(false)
    expect(ops.hasView({ projectId: 'p', flowId: 'none' })).toBe(false)
  })
})

describe('flow attachment work manager — read-only (R1 완료 기준 4, 삭제 부분)', () => {
  it('read-only drops the unsent deletes; writable again does not bring them back; new deletes work again', async () => {
    ops.stageDelete(A, [file('a')])
    ops.setReadOnly(A, true)
    expect(ops.pendingGroup(A, 'a')).toBeUndefined()
    ops.setReadOnly(A, false)
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).not.toHaveBeenCalled()
    ops.stageDelete(A, [file('b')])
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
  })

  it('a Flow turning read-only while a group is being sent: the files not sent yet stay', async () => {
    await ops.refresh(A)
    const first = deferred()
    api.deleteFile.mockImplementationOnce(() => first.promise)
    ops.stageDelete(A, [file('a'), file('b')])
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
    expect([...ops.getFlowState(A).deleting]).toEqual(['a', 'b'])
    ops.setReadOnly(A, true)
    first.resolve({})
    await vi.advanceTimersByTimeAsync(0); await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
    expect(ops.getFlowState(A).deleting.size).toBe(0)
    expect(ops.getFlowState(A).files.map(f => f.key)).toEqual(['b'])
  })

  it('a group being sent: read-only then writable again BEFORE the first answer — the rest is still not sent', async () => {
    await ops.refresh(A)
    const first = deferred()
    api.deleteFile.mockImplementationOnce(() => first.promise)
    ops.stageDelete(A, [file('a'), file('b')])
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledTimes(1) // a is on the way, b waits in the commit loop
    ops.setReadOnly(A, true)
    ops.setReadOnly(A, false) // writable again before a answers
    expect(ops.getFlowState(A).readOnly).toBe(false)
    first.resolve({})
    await vi.advanceTimersByTimeAsync(0); await flushPromises()
    await vi.advanceTimersByTimeAsync(10000); await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
    expect(api.deleteFile).not.toHaveBeenCalledWith('A', expect.objectContaining({ key: 'b' }))
    expect(ops.getFlowState(A).deleting.size).toBe(0)
    expect(ops.getFlowState(A).files.map(f => f.key)).toEqual(['b'])
    // A new 🗑 after unlocking is a new group and is sent.
    ops.stageDelete(A, [file('b')])
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledTimes(2)
    expect(api.deleteFile).toHaveBeenLastCalledWith('A', expect.objectContaining({ key: 'b' }))
  })

  it('the archive state from outside the windows: true drops, null changes nothing, false unlocks', async () => {
    const archived = ref<Record<string, boolean | null>>({ A: null, B: false })
    const disconnect = ops.connectReadOnly((projectId, flowId) => (projectId === 'p' ? archived.value[flowId] ?? null : null))
    ops.stageDelete(A, [file('a')])
    ops.stageDelete(B, [file('b')])
    await nextTick()
    expect(ops.pendingGroup(A, 'a')).toBeDefined()
    archived.value = { A: true, B: false }
    await nextTick()
    expect(ops.pendingGroup(A, 'a')).toBeUndefined()
    expect(ops.getFlowState(A).readOnly).toBe(true)
    archived.value = { A: false, B: false }
    await nextTick()
    expect(ops.getFlowState(A).readOnly).toBe(false)
    disconnect()
    archived.value = { A: true, B: true }
    await nextTick()
    expect(ops.pendingGroup(B, 'b')).toBeDefined()
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile.mock.calls.map(([flow]) => flow)).toEqual(['B'])
  })
})

describe('flow attachment work manager — session end', () => {
  it('the session-end signal drops unsent deletes and moves the generation', async () => {
    ops.startFlowAttachmentOperations()
    ops.stageDelete(A, [file('a')])
    const before = ops.sessionGeneration()
    const stateBefore = ops.getFlowState(A)
    announceAuthSessionEnded()
    expect(ops.sessionGeneration()).toBe(before + 1)
    expect(ops.getFlowState(A)).not.toBe(stateBefore)
    expect(ops.pendingFiles(A)).toEqual([])
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).not.toHaveBeenCalled()
  })

  it('an answer that arrives after the session ended changes nothing', async () => {
    ops.startFlowAttachmentOperations()
    await ops.refresh(A)
    const late = deferred()
    api.deleteFile.mockImplementationOnce(() => late.promise)
    ops.stageDelete(A, [file('a'), file('b')])
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    const old = ops.getFlowState(A)
    window.dispatchEvent(new CustomEvent(AUTH_SESSION_ENDED_EVENT))
    late.resolve({})
    await vi.advanceTimersByTimeAsync(0); await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledTimes(1) // b is not sent for the old session
    expect(old.files.map(f => f.key)).toEqual(['a', 'b'])
    expect(ops.getFlowState(A).files).toEqual([])
  })

  it('a late list read of the old session does not fill the new one', async () => {
    ops.startFlowAttachmentOperations()
    const read = deferred()
    api.readFiles.mockImplementationOnce(() => read.promise)
    const work = ops.refresh(A)
    announceAuthSessionEnded()
    read.resolve([file('z')])
    await work
    expect(ops.getFlowState(A).files).toEqual([])
  })

  it('another user signing in is a new session', async () => {
    ops.startFlowAttachmentOperations()
    ops.stageDelete(A, [file('a')])
    setCurrentUser({ username: 'lee', email: null, isAdmin: false })
    await nextTick()
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).not.toHaveBeenCalled()
  })

  it('sign-out stops the work BEFORE the server logout call', async () => {
    ops.startFlowAttachmentOperations()
    ops.stageDelete(A, [file('a')])
    window.addEventListener(AUTH_SESSION_ENDED_EVENT, () => order.push('ended'), { once: true })
    const { logout } = await import('@/core/auth/logout')
    const realLocation = window.location
    Object.defineProperty(window, 'location', { configurable: true, value: { href: '' } })
    try { await logout() } finally { Object.defineProperty(window, 'location', { configurable: true, value: realLocation }) }
    expect(order).toEqual(['ended', 'serverLogout'])
    expect(ops.pendingFiles(A)).toEqual([])
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).not.toHaveBeenCalled()
  })

  it('every shared token clean-up (also the sign-out of another tab) sends the session-end signal', () => {
    const src = readFileSync(resolve(__dirname, '../../../src/shared/api.ts'), 'utf8')
    const body = /const clearStoredAuth = \(\) => \{([\s\S]*?)\n\}/.exec(src)?.[1] ?? ''
    expect(body).toContain('announceAuthSessionEnded()')
    expect(src).toMatch(/msg\?\.type === 'logout'\) \{\s*clearStoredAuth\(\)/)
  })

  it('import alone installs nothing; start is idempotent and dispose removes the listener', async () => {
    const generation = ops.sessionGeneration()
    announceAuthSessionEnded()
    expect(ops.sessionGeneration()).toBe(generation)
    const dispose = ops.startFlowAttachmentOperations()
    ops.startFlowAttachmentOperations()
    announceAuthSessionEnded()
    expect(ops.sessionGeneration()).toBe(generation + 1)
    dispose()
    const afterDispose = ops.sessionGeneration()
    announceAuthSessionEnded()
    expect(ops.sessionGeneration()).toBe(afterDispose)
  })
})

describe('flow attachment work manager — archive watch for the manager lifetime (R1 완료 기준 4, 삭제 부분)', () => {
  it('watches a Project only while it has pending or in-flight work', async () => {
    const fake = archiveFake()
    ops.startFlowAttachmentOperations({ subscribe: fake.subscribe, readArchived: fake.readArchived })
    expect(ops.watchedProjects()).toEqual([])
    ops.stageDelete(A, [file('a')])
    ops.stageDelete(A2, [file('x')])
    await flushPromises()
    expect(ops.watchedProjects().sort()).toEqual(['p', 'q'])
    expect(fake.subscribe).toHaveBeenCalledWith('p', expect.objectContaining({ events: ['group_view_refresh'] }))
    ops.undoItem(A2, 'x')
    await flushPromises()
    expect(ops.watchedProjects()).toEqual(['p'])
    expect(fake.unsubscribed).toEqual(['q'])
    const slow = deferred()
    api.deleteFile.mockImplementationOnce(() => slow.promise)
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    // The group left the queue and is being sent: still watched (the leave-then-send step does not drop it).
    expect(fake.subscribe).toHaveBeenCalledTimes(2)
    expect(ops.watchedProjects()).toEqual(['p'])
    slow.resolve({})
    await flushPromises()
    expect(ops.watchedProjects()).toEqual([])
    expect(fake.listening('p')).toBe(0)
  })

  it('Designer left (Host gone, no window): an archive event for the Project drops the unsent delete', async () => {
    const fake = archiveFake()
    ops.startFlowAttachmentOperations({ subscribe: fake.subscribe, readArchived: fake.readArchived })
    // The Designer's own path is connected while the user is there, then the Host unmounts (route left).
    const disconnect = ops.connectReadOnly(() => null)
    ops.attachView(A, 'w1')
    ops.stageDelete(A, [file('a')])
    ops.detachView(A, 'w1')
    disconnect()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2000)
    fake.readArchived.mockResolvedValueOnce(true)
    fake.fire('p') // another session archives Flow A
    await flushPromises()
    expect(fake.readArchived).toHaveBeenCalledWith('A')
    expect(ops.getFlowState(A).readOnly).toBe(true)
    expect(ops.pendingGroup(A, 'a')).toBeUndefined()
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).not.toHaveBeenCalled()
  })

  it('moved to another Project: the first Project is still watched; its archive drops only its own delete', async () => {
    const fake = archiveFake()
    ops.startFlowAttachmentOperations({ subscribe: fake.subscribe, readArchived: fake.readArchived })
    // The Host now shows Project q only (its source answers null for p).
    const disconnect = ops.connectReadOnly((projectId, flowId) => (projectId === 'q' ? false : null))
    ops.stageDelete(A, [file('a')])   // Project p
    ops.stageDelete(A2, [file('x')])  // Project q
    await flushPromises()
    fake.readArchived.mockImplementation(async flowId => flowId === 'A')
    fake.fire('p')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile.mock.calls.map(([flow, f]) => `${flow}:${(f as FlowFile).key}`)).toEqual(['A:x'])
    expect(ops.getFlowState(A).readOnly).toBe(true)
    expect(ops.getFlowState(A2).readOnly).toBe(false)
    disconnect()
  })

  it('a stream resync reads the archive state again (an event may have been missed)', async () => {
    const fake = archiveFake()
    ops.startFlowAttachmentOperations({ subscribe: fake.subscribe, readArchived: fake.readArchived })
    ops.stageDelete(A, [file('a')])
    await flushPromises()
    fake.readArchived.mockResolvedValueOnce(true)
    fake.resync('p')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).not.toHaveBeenCalled()
  })

  it('an archive read still running when the five seconds run out: the group waits for it and sends nothing if archived', async () => {
    const fake = archiveFake()
    ops.startFlowAttachmentOperations({ subscribe: fake.subscribe, readArchived: fake.readArchived })
    ops.stageDelete(A, [file('a'), file('b')])
    await flushPromises()
    await vi.advanceTimersByTimeAsync(4900)
    const read = deferred()
    fake.readArchived.mockImplementationOnce(() => read.promise as Promise<boolean>)
    fake.fire('p')
    await vi.advanceTimersByTimeAsync(100); await flushPromises()
    expect(api.deleteFile).not.toHaveBeenCalled()
    read.resolve(true)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).not.toHaveBeenCalled()
    expect(ops.getFlowState(A).deleting.size).toBe(0)
    expect(ops.getFlowState(A).readOnly).toBe(true)
  })

  it('archived during a group being sent, then restored before the first answer: the rest is not sent', async () => {
    const fake = archiveFake()
    ops.startFlowAttachmentOperations({ subscribe: fake.subscribe, readArchived: fake.readArchived })
    await ops.refresh(A)
    const first = deferred()
    api.deleteFile.mockImplementationOnce(() => first.promise)
    ops.stageDelete(A, [file('a'), file('b')])
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
    fake.readArchived.mockResolvedValueOnce(true)
    fake.fire('p'); await flushPromises()       // archived
    expect(ops.getFlowState(A).readOnly).toBe(true)
    fake.readArchived.mockResolvedValueOnce(false)
    fake.fire('p'); await flushPromises()       // restored
    expect(ops.getFlowState(A).readOnly).toBe(false)
    first.resolve({})
    await vi.advanceTimersByTimeAsync(0); await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
    expect(ops.getFlowState(A).files.map(f => f.key)).toEqual(['b'])
  })

  it('a failed archive read (not known) changes nothing: the delete goes out at five seconds', async () => {
    const fake = archiveFake()
    ops.startFlowAttachmentOperations({ subscribe: fake.subscribe, readArchived: fake.readArchived })
    ops.stageDelete(A, [file('a')])
    await flushPromises()
    fake.readArchived.mockRejectedValueOnce(new Error('offline'))
    fake.fire('p'); await flushPromises()
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledTimes(1)
    expect(ops.getFlowState(A).readOnly).toBe(false)
  })

  it('the session end stops every archive watch, and a late archive answer of the old session changes nothing', async () => {
    const fake = archiveFake()
    ops.startFlowAttachmentOperations({ subscribe: fake.subscribe, readArchived: fake.readArchived })
    ops.stageDelete(A, [file('a')])
    await flushPromises()
    const read = deferred()
    fake.readArchived.mockImplementationOnce(() => read.promise as Promise<boolean>)
    fake.fire('p')
    announceAuthSessionEnded()
    expect(ops.watchedProjects()).toEqual([])
    expect(fake.listening('p')).toBe(0)
    read.resolve(true)
    await flushPromises()
    expect(ops.getFlowState(A).readOnly).toBe(false)
  })
})


// R2: delayed responses keep the worker alive across view and permission changes.
describe('Flow upload manager (R2)', () => {
  const incoming = () => Array.from({ length: 5 }, (_, i) => new File([`${i}`], `${i}.txt`))
  const conflict = { response: { data: { error: { code: 'ATTACHMENT_NAME_CONFLICT', details: { key: 'original', content_sha256: 'original-hash' } } } } }
  beforeEach(() => { api.uploadFile.mockReset(); api.uploadFile.mockResolvedValue({}) })

  it('sends all five after immediate close; closed completions mark dirty without list reads', async () => {
    const first = deferred(), files = incoming()
    api.uploadFile.mockReturnValueOnce(first.promise)
    const state = ops.attachView(A, 'w')
    ops.enqueueUpload(A, files)
    ops.detachView(A, 'w')
    await flushPromises()
    expect(state.currentFile).toBe(files[0]); expect(state.pendingCount).toBe(4)
    first.resolve(); await flushPromises()
    expect(api.uploadFile.mock.calls.map(args => args[1])).toEqual(files)
    expect(api.readFiles).not.toHaveBeenCalled()
    expect(state.dirty).toBe(true); expect(state.currentFile).toBeNull(); expect(state.pendingCount).toBe(0)
    ops.attachView(A, 'again'); await ops.refresh(A)
    expect(api.readFiles).toHaveBeenCalledTimes(1); expect(state.dirty).toBe(false)
  })

  it('close cancels only the question file, releases its resolver and ignores its stale answer', async () => {
    api.uploadFile.mockRejectedValueOnce(conflict)
    const state = ops.attachView(A, 'w'), files = incoming()
    ops.enqueueUpload(A, files); await flushPromises()
    const id = state.collision!.id
    ops.detachView(A, 'w'); await flushPromises()
    ops.attachView(A, 'again'); ops.answerCollision(id, 'overwrite'); await flushPromises()
    expect(api.uploadFile.mock.calls.map(args => args[1])).toEqual(files)
    expect(state.cancelledUploads).toEqual(['0.txt']); expect(state.collision).toBeNull()
  })

  it('new conflicts while closed cancel without waiting and later files still upload', async () => {
    api.uploadFile.mockRejectedValueOnce(conflict)
    ops.enqueueUpload(A, incoming()); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(5)
    expect(ops.getFlowState(A).cancelledUploads).toEqual(['0.txt'])
    expect(ops.getFlowState(A).collision).toBeNull()
  })

  it('overwrite retains the first conflict key/hash and a stale id cannot answer a later question', async () => {
    api.uploadFile.mockRejectedValueOnce(conflict).mockRejectedValueOnce(conflict)
    const state = ops.attachView(A, 'w'), files = incoming().slice(0, 2)
    ops.enqueueUpload(A, files); await flushPromises()
    const firstId = state.collision!.id
    ops.answerCollision(firstId, 'cancel'); await flushPromises()
    const secondId = state.collision!.id
    ops.answerCollision(firstId, 'overwrite'); await flushPromises()
    expect(state.collision!.id).toBe(secondId)
    ops.answerCollision(secondId, 'overwrite'); await flushPromises()
    expect(api.uploadFile).toHaveBeenLastCalledWith('A', files[1], 'overwrite', { key: 'original', content_sha256: 'original-hash' })
  })

  it('read-only then writable drops old pending files but reflects an accepted request', async () => {
    const first = deferred(), files = incoming()
    api.uploadFile.mockReturnValueOnce(first.promise)
    ops.enqueueUpload(A, files); await flushPromises()
    ops.setReadOnly(A, true); ops.setReadOnly(A, false)
    first.resolve(); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(1)
    expect(ops.getFlowState(A).dirty).toBe(true)
    ops.enqueueUpload(A, [files[4]]); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(2)
  })

  it('read-only after a collision answer prevents the retry, even if unlocked immediately', async () => {
    api.uploadFile.mockRejectedValueOnce(conflict)
    const state = ops.attachView(A, 'w')
    ops.enqueueUpload(A, incoming()); await flushPromises()
    ops.answerCollision(state.collision!.id, 'overwrite')
    ops.setReadOnly(A, true); ops.setReadOnly(A, false)
    await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(1); expect(state.collision).toBeNull()
  })

  it.each([false, true])('a late conflict after read-only is discarded without a cancellation notice (unlock=%s)', async (unlock) => {
    const first = deferred()
    api.uploadFile.mockReturnValueOnce(first.promise)
    const state = ops.attachView(A, 'w')
    ops.enqueueUpload(A, incoming()); await flushPromises()
    ops.setReadOnly(A, true)
    if (unlock) ops.setReadOnly(A, false)
    first.reject(conflict); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(1)
    expect(state.collision).toBeNull()
    expect(state.pendingCount).toBe(0)
    expect(state.cancelledUploads).toEqual([])
  })

  it('read-only releases an open conflict without recording a user or view cancellation', async () => {
    api.uploadFile.mockRejectedValueOnce(conflict)
    const state = ops.attachView(A, 'w')
    ops.enqueueUpload(A, incoming()); await flushPromises()
    const id = state.collision!.id
    ops.setReadOnly(A, true); ops.setReadOnly(A, false)
    ops.answerCollision(id, 'overwrite'); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(1)
    expect(state.collision).toBeNull()
    expect(state.pendingCount).toBe(0)
    expect(state.cancelledUploads).toEqual([])
  })

  it('archive watch survives close and gates the next request on its archive response', async () => {
    const archive = archiveFake(), check = deferred(), first = deferred()
    archive.readArchived.mockReturnValue(check.promise as Promise<boolean>)
    ops.startFlowAttachmentOperations(archive)
    api.uploadFile.mockReturnValueOnce(first.promise)
    ops.enqueueUpload(A, incoming()); await flushPromises()
    expect(archive.listening('p')).toBe(1)
    archive.fire('p'); first.resolve(); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(1)
    check.resolve(true); await flushPromises()
    expect(ops.getFlowState(A).pendingCount).toBe(0)
    expect(api.uploadFile).toHaveBeenCalledTimes(1)
    expect(archive.listening('p')).toBe(0)
  })

  it('session disposal releases a collision and ignores a late accepted response', async () => {
    const late = deferred()
    api.uploadFile.mockReturnValueOnce(late.promise)
    const old = ops.getFlowState(A)
    ops.enqueueUpload(A, incoming()); await flushPromises()
    ops.stopSession(); late.resolve(); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(1)
    expect(old.currentFile).toBeNull(); expect(old.dirty).toBe(false)
    api.uploadFile.mockRejectedValueOnce(conflict)
    const state = ops.attachView(A, 'w')
    ops.enqueueUpload(A, incoming()); await flushPromises()
    expect(state.collision).not.toBeNull()
    ops.stopSession(); await flushPromises()
    expect(state.collision).toBeNull(); expect(state.pendingCount).toBe(0)
  })

  it('list refresh never blocks the next upload and enqueue does not erase previous failures', async () => {
    const slowRead = deferred()
    api.readFiles.mockReturnValue(slowRead.promise)
    const state = ops.attachView(A, 'w')
    ops.actionFailed(A, 'download', new Error('previous'), ['old.txt'])
    const wait = deferred()
    api.uploadFile.mockReturnValueOnce(wait.promise)
    ops.enqueueUpload(A, incoming()); await flushPromises()
    // Starting an upload erases nothing; only a finished, successful one does (R3).
    expect(state.errors).toHaveLength(1)
    wait.resolve(); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(5)
    expect(api.readFiles).toHaveBeenCalledTimes(5)
    expect(state.errors).toEqual([])
    slowRead.resolve([]); await flushPromises()
  })

  it('closed upload failures are stored in their Flow and do not stop the remaining files', async () => {
    api.uploadFile.mockRejectedValueOnce({ response: { data: { error: { message: 'upload failed' } } } })
    ops.enqueueUpload(A, incoming()); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(5)
    expect(ops.getFlowState(A).errors).toMatchObject([{ kind: 'upload', targets: ['0.txt'], text: 'upload failed' }])
    expect(ops.getFlowState(B).errors).toEqual([])
  })
})
