import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { getRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  getRequest,
}))

describe('project store project visibility contracts', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    getRequest.mockReset()
  })

  it('fetchProjects requests active projects for selectors', async () => {
    getRequest.mockResolvedValueOnce({
      data: { projects: [{ project_id: 'alpha', project_name: 'Alpha', is_active: 1 }] },
    })
    const { useProjectStore } = await import('@main/stores/project')
    const store = useProjectStore()

    await store.fetchProjects()

    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects', { status: 'active' })
    expect(store.projects.map((p) => p.project_id)).toEqual(['alpha'])
    expect(store.currentProjectId).toBe('alpha')
  })

  it('fetchAllProjects retains archived projects for management and filters activeProjects', async () => {
    getRequest.mockResolvedValueOnce({
      data: {
        projects: [
          { project_id: 'alpha', project_name: 'Alpha', is_active: 1 },
          { project_id: 'old', project_name: 'Old', is_active: 0 },
        ],
      },
    })
    const { useProjectStore } = await import('@main/stores/project')
    const store = useProjectStore()

    await store.fetchAllProjects()

    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects', { status: 'all' })
    expect(store.projects.map((p) => p.project_id)).toEqual(['alpha', 'old'])
    expect(store.activeProjects.map((p) => p.project_id)).toEqual(['alpha'])
  })
  it('keeps an archived current project id while excluding it from selector options', async () => {
    localStorage.setItem('fg_current_project_id', 'old')
    getRequest.mockResolvedValueOnce({
      data: { projects: [{ project_id: 'alpha', project_name: 'Alpha', is_active: 1 }] },
    })
    const { useProjectStore } = await import('@main/stores/project')
    const store = useProjectStore()

    await store.fetchProjects()

    expect(store.currentProjectId).toBe('old')
    expect(localStorage.getItem('fg_current_project_id')).toBe('old')
    expect(store.activeProjects.map((p) => p.project_id)).toEqual(['alpha'])
  })
})
