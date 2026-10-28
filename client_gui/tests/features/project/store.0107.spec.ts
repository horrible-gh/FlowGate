/**
 * flowgate.v02.0107 T#1 — the project store's Designer Git display read of a Flow work base (D §3.1,
 * §6.2-6 / 7). It hands back the existing GET /groups/{gid}/work-base answer and writes or
 * invalidates no shared state — knownGitEnabled, the work-base options cache and session, the
 * group trees, the project Git status — whatever the answer is: Git on or off, another project,
 * late, failed or malformed. The 0104 settings loader (loadFlowWorkBase) keeps its own contract.
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@shared/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@shared/api')>()),
  default: { get },
  getRequest: get,
}))

import { setCurrentUser } from '@/core/auth/currentUser'
import { useProjectStore } from '@/features/project'

function deferred<T = unknown>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

// Project `p` keeps `main` as its base; its Flow `p.m.1` works on `feature/v0.2.0`.
const workBase = (overrides: Record<string, unknown> = {}) => ({
  data: {
    group_id: 'p.m.1',
    project_id: 'p',
    git_enabled: true,
    work_base_ref: 'feature/v0.2.0',
    effective_work_base_ref: 'feature/v0.2.0',
    work_base_locked: false,
    lock_evidence: [],
    work_base_editable: true,
    reason: null,
    can_edit: true,
    ...overrides,
  },
})
const options = (gitEnabled: boolean) => ({
  data: { git_enabled: gitEnabled, base_branch: 'main', branches: [{ name: 'main', is_project_base: true }] },
})
const httpError = (status: number) => ({ response: { status, data: { detail: { code: 'refused', message: 'x' } } } })

const WORK_BASE_URL = /\/groups\/[^/]+\/work-base$/
const urls = () => get.mock.calls.map((call) => String(call[0]))

let store: ReturnType<typeof useProjectStore>

beforeEach(() => {
  setActivePinia(createPinia())
  setCurrentUser({ username: 'alice', email: null, isAdmin: false })
  get.mockReset().mockImplementation(async (url: string) => {
    if (url.endsWith('/projects/p/git/work-base-options')) return options(true)
    if (url.endsWith('/projects/q/git/work-base-options')) return options(false)
    if (url.endsWith('/groups/tree')) return { data: { data: { nodes: [] } } }
    if (WORK_BASE_URL.test(url)) return workBase()
    throw new Error(`unexpected GET ${url}`)
  })
  store = useProjectStore()
})

afterEach(() => {
  store.$dispose()
  setCurrentUser(null)
})

// The shared state of two projects the display read must leave as it is.
async function seedShared() {
  const pOptions = await store.loadWorkBaseOptions('p')
  const qOptions = await store.loadWorkBaseOptions('q')
  const tree = await store.fetchGroupTree('p')
  const trees = await Promise.all([
    store.fetchGroupTree('p', { includeTerminal: true }),
    store.fetchGroupTree('q'),
    store.fetchGroupTree('q', { includeTerminal: true }),
  ])
  get.mockClear()
  return {
    known: { ...store.knownGitEnabled },
    tree,
    trees,
    pOptions,
    qOptions,
  }
}

async function expectSharedUnchanged(seed: Awaited<ReturnType<typeof seedShared>>) {
  expect(store.knownGitEnabled).toEqual(seed.known)
  expect(store.knownGitEnabled).toEqual({ p: true, q: false })
  expect(store.getCachedGroupTree('p')).toBe(seed.tree)
  expect(store.getCachedGroupTree('p', true)).toBe(seed.trees[0])
  expect(store.getCachedGroupTree('q')).toBe(seed.trees[1])
  expect(store.getCachedGroupTree('q', true)).toBe(seed.trees[2])
  // The options of both projects still answer from the cache: no session / version was bumped.
  const before = get.mock.calls.length
  expect(await store.loadWorkBaseOptions('p')).toBe(seed.pOptions)
  expect(await store.loadWorkBaseOptions('q')).toBe(seed.qOptions)
  expect(get.mock.calls.length).toBe(before)
  // Only the work-base GET went out: no git/status, options or group tree read.
  expect(urls().every((url) => WORK_BASE_URL.test(url))).toBe(true)
}

describe('project store — readFlowWorkBaseForDisplay (flowgate.v02.0107 D §3.1)', () => {
  it('a pending display read and its delayed answer preserve both projects and both tree variants', async () => {
    const seed = await seedShared()
    const late = deferred()
    get.mockReturnValueOnce(late.promise)
    const read = store.readFlowWorkBaseForDisplay('p.m.1')
    await expectSharedUnchanged(seed)
    late.resolve(workBase({ project_id: 'q', git_enabled: true }))
    await read
    await expectSharedUnchanged(seed)
  })

  it('returns the existing work-base answer through the same GET, uncached', async () => {
    const seed = await seedShared()
    const first = await store.readFlowWorkBaseForDisplay('p.m.1')
    expect(first).toMatchObject({ groupId: 'p.m.1', projectId: 'p', effectiveWorkBaseRef: 'feature/v0.2.0' })
    await store.readFlowWorkBaseForDisplay('p.m.1')
    expect(urls()).toEqual(['/api/v1/groups/p.m.1/work-base', '/api/v1/groups/p.m.1/work-base'])
    await expectSharedUnchanged(seed)
  })

  it('a Git-off answer does not mark the project Git off', async () => {
    const seed = await seedShared()
    get.mockResolvedValueOnce(workBase({ git_enabled: false, work_base_ref: null, effective_work_base_ref: null }))
    const answer = await store.readFlowWorkBaseForDisplay('p.m.1')
    expect(answer).toMatchObject({ gitEnabled: false, effectiveWorkBaseRef: null })
    await expectSharedUnchanged(seed)
  })

  it('an answer of another project changes nothing of that project either', async () => {
    const seed = await seedShared()
    get.mockResolvedValueOnce(workBase({ project_id: 'q', git_enabled: true }))
    const answer = await store.readFlowWorkBaseForDisplay('p.m.1')
    // The store hands it back as it is; the Git session refuses it (gitWorktreeSession.0107.spec.ts).
    expect(answer.projectId).toBe('q')
    expect(store.knownGitEnabled.q).toBe(false)
    await expectSharedUnchanged(seed)
  })

  it('a late answer after the options were invalidated refills nothing', async () => {
    const seed = await seedShared()
    const late = deferred()
    get.mockReturnValueOnce(late.promise)
    const read = store.readFlowWorkBaseForDisplay('p.m.1')
    store.invalidateWorkBaseOptions('p')
    late.resolve(workBase({ git_enabled: false }))
    await read
    expect(store.knownGitEnabled).toEqual({ q: false })
    expect(seed.known).toEqual({ p: true, q: false })
  })

  it('a group mismatch, a malformed answer, 403 / 404 and a network failure reject and change nothing', async () => {
    const seed = await seedShared()
    get
      .mockResolvedValueOnce(workBase({ group_id: 'p.m.2' }))
      .mockResolvedValueOnce(workBase({ effective_work_base_ref: 7 }))
      .mockRejectedValueOnce(httpError(403))
      .mockRejectedValueOnce(httpError(404))
      .mockRejectedValueOnce(new Error('Network Error'))
    for (let i = 0; i < 5; i += 1) await expect(store.readFlowWorkBaseForDisplay('p.m.1')).rejects.toBeInstanceOf(Error)
    await expect(store.readFlowWorkBaseForDisplay('p.m.1')).resolves.toMatchObject({ groupId: 'p.m.1' })
    await expectSharedUnchanged(seed)
  })

  it('the 0104 settings loader keeps writing the Git knowledge (unchanged contract)', async () => {
    get.mockResolvedValueOnce(workBase({ git_enabled: false }))
    await store.loadFlowWorkBase('p.m.1')
    expect(store.knownGitEnabled.p).toBe(false)
    store.invalidateWorkBaseOptions('p')
    expect(store.knownGitEnabled.p).toBeUndefined()
    get.mockResolvedValueOnce(workBase({ git_enabled: true }))
    await store.readFlowWorkBaseForDisplay('p.m.1')
    expect(store.knownGitEnabled.p).toBeUndefined()
  })
})
