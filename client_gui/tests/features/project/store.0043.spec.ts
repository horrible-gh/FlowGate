/**
 * flowgate.v02.0043 — what the project store adds for the Designer File menu: the recent
 * Project / Group / Flow lists (recentStorage.ts), Project 닫기, opening a recent Project the way
 * the Dialog's "열기" does, the Dialog open that asks for the create child, and the Group / Flow
 * creates that invalidate the cached group tree.
 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Project } from '@/features/project/types'

const fetchActiveProjects = vi.hoisted(() => vi.fn())
const createProjectApi = vi.hoisted(() => vi.fn())
const createGroupApi = vi.hoisted(() => vi.fn())
const createFlowApi = vi.hoisted(() => vi.fn())
const suggestProjectSlug = vi.hoisted(() => vi.fn())
const fetchProjectGroupTree = vi.hoisted(() => vi.fn())
const currentUser = vi.hoisted(() => ({ value: null as { username: string; email: string | null; isAdmin: boolean } | null }))

vi.mock('@/features/project/api', () => ({
  fetchActiveProjects,
  createProject: createProjectApi,
  createGroup: createGroupApi,
  createFlow: createFlowApi,
  suggestProjectSlug,
  fetchProjectGroupTree,
}))
vi.mock('@/core/auth/currentUser', () => ({ useCurrentUser: () => ({ user: currentUser }) }))

import { useProjectStore } from '@/features/project/store'
import ProjectDialog from '@/features/project/ProjectDialog.vue'
import {
  RECENT_FLOWS_KEY,
  RECENT_GROUPS_KEY,
  RECENT_LIMIT,
  RECENT_PROJECTS_KEY,
  pushRecent,
  readRecentFlows,
  readRecentGroups,
  readRecentProjects,
} from '@/features/project/recentStorage'
import './projectI18n'

function project(id: string, name = id): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

const stored = (key: string) => JSON.parse(localStorage.getItem(key) ?? 'null')

beforeEach(() => {
  fetchActiveProjects.mockReset().mockResolvedValue([])
  createProjectApi.mockReset()
  createGroupApi.mockReset()
  createFlowApi.mockReset()
  suggestProjectSlug.mockReset().mockResolvedValue(null)
  fetchProjectGroupTree.mockReset().mockResolvedValue([])
  currentUser.value = { username: 'member', email: null, isAdmin: false }
  localStorage.clear()
  setActivePinia(createPinia())
})

afterEach(() => {
  document.body.querySelectorAll('.ui-modal-overlay').forEach((el) => el.remove())
})

describe('recentStorage', () => {
  it('pushRecent puts the entry first, drops an older one with the same id and caps the list', () => {
    const list = Array.from({ length: RECENT_LIMIT }, (_, i) => ({ id: `p${i}`, name: `P${i}` }))
    const next = pushRecent(list, { id: 'p3', name: 'P3 again' })
    expect(next).toHaveLength(RECENT_LIMIT)
    expect(next[0]).toEqual({ id: 'p3', name: 'P3 again' })
    expect(next.filter((e) => e.id === 'p3')).toHaveLength(1)
    const overflow = pushRecent(next, { id: 'new', name: 'New' })
    expect(overflow).toHaveLength(RECENT_LIMIT)
    expect(overflow[0].id).toBe('new')
    expect(overflow.some((e) => e.id === `p${RECENT_LIMIT - 1}`)).toBe(false)
  })

  it('reads only well-formed entries and treats broken storage as empty', () => {
    localStorage.setItem(RECENT_PROJECTS_KEY, JSON.stringify([{ id: 'a', name: 'A' }, { id: 'b' }, 'x', null]))
    localStorage.setItem(RECENT_GROUPS_KEY, '{not json')
    localStorage.setItem(RECENT_FLOWS_KEY, JSON.stringify([{ id: 'f', name: 'F', projectId: 'p' }, { id: 'g', name: 'G', projectId: 'p', groupId: 'm' }]))
    expect(readRecentProjects()).toEqual([{ id: 'a', name: 'A' }])
    expect(readRecentGroups()).toEqual([])
    expect(readRecentFlows()).toEqual([{ id: 'g', name: 'G', projectId: 'p', groupId: 'm' }])
  })
})

describe('recent lists in the store', () => {
  it('start from storage and record opened Projects, Groups and Flows, newest first, persisted', () => {
    localStorage.setItem(RECENT_PROJECTS_KEY, JSON.stringify([{ id: 'old', name: 'Old' }]))
    const store = useProjectStore()
    expect(store.recentProjects).toEqual([{ id: 'old', name: 'Old' }])

    store.setCurrentProject(project('p', 'Alpha'))
    expect(store.recentProjects).toEqual([{ id: 'p', name: 'Alpha' }, { id: 'old', name: 'Old' }])
    expect(stored(RECENT_PROJECTS_KEY)).toEqual([{ id: 'p', name: 'Alpha' }, { id: 'old', name: 'Old' }])

    store.recordRecentGroup({ id: 'module:p:ui', name: 'UI', projectId: 'p' })
    store.recordRecentFlow({ id: 'p.ui.0001', name: 'Canvas', projectId: 'p', groupId: 'module:p:ui' })
    store.recordRecentFlow({ id: 'p.ui.0002', name: 'Designer', projectId: 'p', groupId: 'module:p:ui' })
    store.recordRecentFlow({ id: 'p.ui.0001', name: 'Canvas 2', projectId: 'p', groupId: 'module:p:ui' })
    expect(store.recentGroups).toEqual([{ id: 'module:p:ui', name: 'UI', projectId: 'p' }])
    expect(store.recentFlows.map((f) => [f.id, f.name])).toEqual([['p.ui.0001', 'Canvas 2'], ['p.ui.0002', 'Designer']])
    expect(stored(RECENT_GROUPS_KEY)).toEqual([{ id: 'module:p:ui', name: 'UI', projectId: 'p' }])
    expect(stored(RECENT_FLOWS_KEY)).toHaveLength(2)
  })

  it('openRecentProject makes the Project current, raises the explicit-open signal and records it', () => {
    const store = useProjectStore()
    store.openRecentProject({ id: 'q', name: 'Beta' })
    expect(store.currentProjectId).toBe('q')
    expect(localStorage.getItem('fg_current_project_id')).toBe('q')
    expect(store.explicitOpenProjectId).toBe('q')
    expect(store.takeExplicitOpen()).toBe('q')
    expect(store.explicitOpenProjectId).toBeNull()
    expect(store.recentProjects).toEqual([{ id: 'q', name: 'Beta' }])
  })

  it('closeCurrentProject clears the current Project and its stored id, keeping the recent list', () => {
    const store = useProjectStore()
    store.setCurrentProject(project('p', 'Alpha'))
    store.closeCurrentProject()
    expect(store.currentProjectId).toBeNull()
    expect(localStorage.getItem('fg_current_project_id')).toBeNull()
    expect(store.recentProjects).toEqual([{ id: 'p', name: 'Alpha' }])
  })
})

describe('openDialog with the create request', () => {
  it('is readable once, and only when asked for; closeDialog drops it', () => {
    const store = useProjectStore()
    store.openDialog()
    expect(store.takeCreateRequest()).toBe(false)
    store.closeDialog()

    store.openDialog({ create: true })
    expect(store.dialogOpen).toBe(true)
    expect(store.takeCreateRequest()).toBe(true)
    expect(store.takeCreateRequest()).toBe(false)
    store.closeDialog()

    store.openDialog({ create: true })
    store.closeDialog()
    expect(store.takeCreateRequest()).toBe(false)
  })

  it('ProjectDialog opens the create child for an admin and not for anyone else', async () => {
    currentUser.value = { username: 'admin', email: null, isAdmin: true }
    const wrapper = mount(ProjectDialog, { attachTo: document.body })
    const store = useProjectStore()
    store.openDialog({ create: true })
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    expect(document.querySelectorAll('.ui-modal-overlay')).toHaveLength(2)
    expect(document.body.textContent).toContain('새 프로젝트')
    store.closeDialog()
    await wrapper.vm.$nextTick()
    expect(document.querySelectorAll('.ui-modal-overlay')).toHaveLength(0)

    // A plain open afterwards shows the list only.
    store.openDialog()
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    expect(document.querySelectorAll('.ui-modal-overlay')).toHaveLength(1)
    store.closeDialog()
    wrapper.unmount()

    currentUser.value = { username: 'member', email: null, isAdmin: false }
    const member = mount(ProjectDialog, { attachTo: document.body })
    useProjectStore().openDialog({ create: true })
    await member.vm.$nextTick()
    await member.vm.$nextTick()
    expect(document.querySelectorAll('.ui-modal-overlay')).toHaveLength(1)
    expect(useProjectStore().takeCreateRequest()).toBe(false)
    member.unmount()
  })
})

describe('createGroup / createFlow', () => {
  it('call the API and drop the cached group tree of that Project', async () => {
    const store = useProjectStore()
    fetchProjectGroupTree.mockResolvedValue([{ id: 'project:p', parentId: null, kind: 'project', label: 'Alpha', title: null, number: null }])
    await store.fetchGroupTree('p')
    await store.fetchGroupTree('p', { includeTerminal: true })
    await store.fetchGroupTree('q')
    expect(store.getCachedGroupTree('p')).toBeDefined()

    createGroupApi.mockResolvedValue({ id: 'module:p:infra', name: 'infra', title: 'Infra' })
    await expect(store.createGroup('p', { name: 'infra', title: 'Infra' })).resolves.toEqual({ id: 'module:p:infra', name: 'infra', title: 'Infra' })
    expect(createGroupApi).toHaveBeenCalledWith('p', { name: 'infra', title: 'Infra' })
    expect(store.getCachedGroupTree('p')).toBeUndefined()
    expect(store.getCachedGroupTree('p', true)).toBeUndefined()
    expect(store.getCachedGroupTree('q')).toBeDefined()

    await store.fetchGroupTree('q')
    createFlowApi.mockResolvedValue({ id: 'q.m.0002', title: 'Flow' })
    await expect(store.createFlow({ projectId: 'q', moduleName: 'm', title: 'Flow' })).resolves.toEqual({ id: 'q.m.0002', title: 'Flow' })
    expect(createFlowApi).toHaveBeenCalledWith({ projectId: 'q', moduleName: 'm', title: 'Flow' })
    expect(store.getCachedGroupTree('q')).toBeUndefined()
  })

  it('a failed create leaves the cache alone and rethrows', async () => {
    const store = useProjectStore()
    fetchProjectGroupTree.mockResolvedValue([])
    await store.fetchGroupTree('p')
    createGroupApi.mockRejectedValue(new Error('Failed to create the group.'))
    await expect(store.createGroup('p', { name: 'x', title: 'X' })).rejects.toThrow('Failed to create the group.')
    expect(store.getCachedGroupTree('p')).toBeDefined()
  })

  it('suggestSlug reuses the identifier suggestion', async () => {
    suggestProjectSlug.mockResolvedValue('infra')
    await expect(useProjectStore().suggestSlug('Infra')).resolves.toBe('infra')
    expect(suggestProjectSlug).toHaveBeenCalledWith('Infra')
  })
})
