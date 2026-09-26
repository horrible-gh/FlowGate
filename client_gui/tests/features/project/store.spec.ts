import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { Project } from '@/features/project/types'

const fetchActiveProjects = vi.hoisted(() => vi.fn())
const createProjectApi = vi.hoisted(() => vi.fn())

vi.mock('@/features/project/api', () => ({
  fetchActiveProjects,
  createProject: createProjectApi,
}))

import { useProjectStore } from '@/features/project/store'

const CURRENT_KEY = 'fg_current_project_id'
const BRANCH_KEY = 'fg_current_branch'

function project(id: string, name = id): Project {
  return {
    id,
    name,
    description: '',
    color: null,
    isActive: true,
    createdAt: null,
    updatedAt: null,
    modules: [],
  }
}

beforeEach(() => {
  fetchActiveProjects.mockReset()
  createProjectApi.mockReset()
  localStorage.clear()
  setActivePinia(createPinia())
})

describe('fetchProjects', () => {
  it('normalizes the active list into store state', async () => {
    fetchActiveProjects.mockResolvedValue([project('a'), project('b')])
    const store = useProjectStore()
    await store.fetchProjects()
    expect(store.projects.map((p) => p.id)).toEqual(['a', 'b'])
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('surfaces the propagated error message and clears loading', async () => {
    fetchActiveProjects.mockRejectedValue(new Error('offline'))
    const store = useProjectStore()
    await store.fetchProjects()
    expect(store.error).toBe('offline')
    expect(store.loading).toBe(false)
    expect(store.projects).toEqual([])
  })

  it('does not let a stale earlier fetch overwrite a newer one', async () => {
    let resolveFirst: (value: Project[]) => void = () => {}
    fetchActiveProjects
      .mockImplementationOnce(() => new Promise<Project[]>((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce([project('second')])

    const store = useProjectStore()
    const first = store.fetchProjects()
    const second = store.fetchProjects()
    await second
    resolveFirst([project('first')])
    await first

    expect(store.projects.map((p) => p.id)).toEqual(['second'])
  })
})

describe('current project fallback (WP0005 T#1 §4)', () => {
  it('keeps a saved current project that is still active', async () => {
    localStorage.setItem(CURRENT_KEY, 'b')
    fetchActiveProjects.mockResolvedValue([project('a'), project('b')])
    const store = useProjectStore()
    await store.fetchProjects()
    expect(store.currentProject?.id).toBe('b')
  })

  it('falls back to the first active project when the saved id is invalid', async () => {
    localStorage.setItem(CURRENT_KEY, 'ghost')
    fetchActiveProjects.mockResolvedValue([project('a'), project('b')])
    const store = useProjectStore()
    await store.fetchProjects()
    expect(store.currentProject?.id).toBe('a')
    expect(localStorage.getItem(CURRENT_KEY)).toBe('a')
  })

  it('falls back to the first active project when nothing was saved', async () => {
    fetchActiveProjects.mockResolvedValue([project('a')])
    const store = useProjectStore()
    await store.fetchProjects()
    expect(store.currentProject?.id).toBe('a')
  })

  it('clears current when there are no active projects', async () => {
    localStorage.setItem(CURRENT_KEY, 'a')
    fetchActiveProjects.mockResolvedValue([])
    const store = useProjectStore()
    await store.fetchProjects()
    expect(store.currentProject).toBeNull()
    expect(localStorage.getItem(CURRENT_KEY)).toBeNull()
  })
})

describe('setCurrentProject', () => {
  it('changes current project without touching Git branch state', async () => {
    localStorage.setItem(BRANCH_KEY, 'feature/untouched')
    fetchActiveProjects.mockResolvedValue([project('a'), project('b')])
    const store = useProjectStore()
    await store.fetchProjects()

    store.setCurrentProject(project('b'))

    expect(store.currentProject?.id).toBe('b')
    expect(localStorage.getItem(CURRENT_KEY)).toBe('b')
    expect(localStorage.getItem(BRANCH_KEY)).toBe('feature/untouched')
  })
})

describe('Dialog open/close', () => {
  it('opening the Dialog triggers a list fetch', () => {
    fetchActiveProjects.mockResolvedValue([project('a')])
    const store = useProjectStore()
    store.openDialog()
    expect(store.dialogOpen).toBe(true)
    expect(fetchActiveProjects).toHaveBeenCalledTimes(1)
  })

  it('closing clears loading/error', async () => {
    fetchActiveProjects.mockRejectedValue(new Error('offline'))
    const store = useProjectStore()
    await store.fetchProjects()
    expect(store.error).toBe('offline')

    store.closeDialog()

    expect(store.dialogOpen).toBe(false)
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('a stale fetch that resolves after close does not resurrect loading/error or reopen the Dialog', async () => {
    let resolveFetch: (value: Project[]) => void = () => {}
    fetchActiveProjects.mockImplementationOnce(
      () => new Promise<Project[]>((resolve) => { resolveFetch = resolve }),
    )
    const store = useProjectStore()

    store.openDialog()
    expect(store.loading).toBe(true)

    store.closeDialog()
    resolveFetch([project('a')])
    await Promise.resolve()
    await Promise.resolve()

    expect(store.dialogOpen).toBe(false)
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
    expect(store.projects).toEqual([])
  })
})

describe('createProject', () => {
  it('creates, then refreshes the active list, without changing current project', async () => {
    fetchActiveProjects
      .mockResolvedValueOnce([project('a')])
      .mockResolvedValueOnce([project('a'), project('new', 'New')])
    createProjectApi.mockResolvedValue(project('new', 'New'))

    const store = useProjectStore()
    await store.fetchProjects()
    const priorCurrentId = store.currentProject?.id

    const created = await store.createProject({ name: 'New', id: 'new' })

    expect(created.id).toBe('new')
    expect(store.projects.map((p) => p.id)).toEqual(['a', 'new'])
    expect(store.currentProject?.id).toBe(priorCurrentId)
  })

  it('propagates a create failure without touching store.error', async () => {
    fetchActiveProjects.mockResolvedValue([project('a')])
    createProjectApi.mockRejectedValue(new Error('duplicate name'))

    const store = useProjectStore()
    await store.fetchProjects()

    await expect(store.createProject({ name: 'Dup', id: 'dup' })).rejects.toThrow('duplicate name')
    expect(store.error).toBeNull()
  })

  it('skips the post-create refresh when the Dialog closes while the create request is in flight', async () => {
    let resolveCreate: (value: Project) => void = () => {}
    createProjectApi.mockImplementationOnce(
      () => new Promise<Project>((resolve) => { resolveCreate = resolve }),
    )
    fetchActiveProjects.mockResolvedValue([project('a')])

    const store = useProjectStore()
    store.openDialog()
    await Promise.resolve()
    await Promise.resolve()
    fetchActiveProjects.mockClear()

    const createCall = store.createProject({ name: 'New', id: 'new' })
    store.closeDialog()
    resolveCreate(project('new', 'New'))
    await createCall

    expect(fetchActiveProjects).not.toHaveBeenCalled()
    expect(store.dialogOpen).toBe(false)
  })

  it('clears loading once a create fails while an earlier fetch it invalidated is still in flight', async () => {
    let resolveFetch: (value: Project[]) => void = () => {}
    fetchActiveProjects.mockImplementationOnce(
      () => new Promise<Project[]>((resolve) => { resolveFetch = resolve }),
    )
    createProjectApi.mockRejectedValueOnce(new Error('duplicate name'))

    const store = useProjectStore()
    const fetchCall = store.fetchProjects()
    expect(store.loading).toBe(true)

    await expect(store.createProject({ name: 'Dup', id: 'dup' })).rejects.toThrow('duplicate name')
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()

    resolveFetch([project('a')])
    await fetchCall
    expect(store.loading).toBe(false)
  })

  it('returns null for a create response invalidated by a close/reopen before it resolves', async () => {
    let resolveCreate: (value: Project) => void = () => {}
    createProjectApi.mockImplementationOnce(
      () => new Promise<Project>((resolve) => { resolveCreate = resolve }),
    )
    fetchActiveProjects.mockResolvedValue([project('a')])

    const store = useProjectStore()
    store.openDialog()
    await Promise.resolve()
    await Promise.resolve()

    const createCall = store.createProject({ name: 'New', id: 'new' })
    store.closeDialog()
    store.openDialog()
    await Promise.resolve()
    await Promise.resolve()

    resolveCreate(project('new', 'New'))
    const result = await createCall

    expect(result).toBeNull()
    expect(store.projects.map((p) => p.id)).toEqual(['a'])
  })

  it('returns null when a close/reopen lands while the post-create list refresh is still in flight', async () => {
    // create's own API call resolves promptly; it is the *refresh that follows it*
    // (fetchProjects's internal fetchActiveProjects call) that is held open here, so we can
    // land a close/reopen in the gap between "create succeeded" and "refresh applied".
    let resolveRefresh: (value: Project[]) => void = () => {}
    fetchActiveProjects
      .mockResolvedValueOnce([]) // openDialog's initial fetch
      .mockImplementationOnce(
        () => new Promise<Project[]>((resolve) => { resolveRefresh = resolve }),
      ) // post-create refresh, held open
      .mockResolvedValueOnce([project('a')]) // reopen's fresh fetch
    createProjectApi.mockResolvedValue(project('new', 'New'))

    const store = useProjectStore()
    store.openDialog()
    await Promise.resolve()
    await Promise.resolve()

    const createCall = store.createProject({ name: 'New', id: 'new' })
    // let createProjectApi resolve and the post-create refresh reach fetchActiveProjects
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()

    store.closeDialog()
    store.openDialog()
    await Promise.resolve()
    await Promise.resolve()

    resolveRefresh([project('new', 'New')])
    const result = await createCall

    expect(result).toBeNull()
    expect(store.projects.map((p) => p.id)).toEqual(['a'])
  })

  it('returns null instead of throwing when a create rejects after being invalidated by a close/reopen', async () => {
    let rejectCreate: (err: Error) => void = () => {}
    createProjectApi.mockImplementationOnce(
      () => new Promise<Project>((_resolve, reject) => { rejectCreate = reject }),
    )
    fetchActiveProjects.mockResolvedValue([project('a')])

    const store = useProjectStore()
    store.openDialog()
    await Promise.resolve()
    await Promise.resolve()

    const createCall = store.createProject({ name: 'New', id: 'new' })
    store.closeDialog()
    store.openDialog()
    await Promise.resolve()
    await Promise.resolve()

    rejectCreate(new Error('duplicate name'))
    const result = await createCall

    expect(result).toBeNull()
    expect(store.error).toBeNull()
    expect(store.projects.map((p) => p.id)).toEqual(['a'])
  })

  it('does not set current project as a side effect of the first create from an empty list', async () => {
    fetchActiveProjects
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([project('new', 'New')])
    createProjectApi.mockResolvedValue(project('new', 'New'))

    const store = useProjectStore()
    await store.fetchProjects()
    expect(store.currentProject).toBeNull()

    const created = await store.createProject({ name: 'New', id: 'new' })

    expect(created?.id).toBe('new')
    expect(store.projects.map((p) => p.id)).toEqual(['new'])
    expect(store.currentProject).toBeNull()
    expect(localStorage.getItem(CURRENT_KEY)).toBeNull()
  })
})
