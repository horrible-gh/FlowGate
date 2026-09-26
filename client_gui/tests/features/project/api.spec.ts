import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
const extractApiErrorMessage = vi.hoisted(() => vi.fn((_error: unknown, fallback: string) => fallback))

vi.mock('@shared/api', () => ({ getRequest, postRequest, extractApiErrorMessage }))

import { createProject, fetchActiveProjects, suggestProjectSlug } from '@/features/project/api'

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
  extractApiErrorMessage.mockClear()
})

describe('fetchActiveProjects', () => {
  it('requests the active list and normalizes snake_case fields, dropping __SYSTEM__', async () => {
    getRequest.mockResolvedValue({
      data: {
        projects: [
          {
            project_id: 'demo',
            project_name: 'Demo',
            description: null,
            color: '#112233',
            is_active: 1,
            created_at: '2026-01-01T00:00:00+09:00',
            updated_at: '2026-01-02T00:00:00+09:00',
            modules: [{ name: 'flow', title: 'Flow' }],
          },
          { project_id: '__SYSTEM__', project_name: 'system', is_active: 1 },
        ],
      },
    })

    const projects = await fetchActiveProjects()

    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects', { status: 'active' })
    expect(projects).toEqual([
      {
        id: 'demo',
        name: 'Demo',
        description: '',
        color: '#112233',
        isActive: true,
        createdAt: '2026-01-01T00:00:00+09:00',
        updatedAt: '2026-01-02T00:00:00+09:00',
        modules: [{ name: 'flow', title: 'Flow' }],
      },
    ])
  })

  it('accepts a bare array response body', async () => {
    getRequest.mockResolvedValue({
      data: [{ project_id: 'a', project_name: 'A', is_active: 1 }],
    })
    const projects = await fetchActiveProjects()
    expect(projects.map((p) => p.id)).toEqual(['a'])
  })

  it('propagates a normalized error message on failure', async () => {
    getRequest.mockRejectedValue(new Error('network down'))
    extractApiErrorMessage.mockReturnValueOnce('Failed to load projects.')
    await expect(fetchActiveProjects()).rejects.toThrow('Failed to load projects.')
    expect(extractApiErrorMessage).toHaveBeenCalledWith(expect.any(Error), 'Failed to load projects.')
  })
})

describe('createProject', () => {
  it('sends only the fields the backend actually reads', async () => {
    postRequest.mockResolvedValue({
      data: { project_id: 'demo', project_name: 'Demo', description: '', color: '#112233', is_active: 1 },
    })

    const created = await createProject({ name: 'Demo', id: 'demo', description: '', color: '#112233' })

    expect(postRequest).toHaveBeenCalledWith('/api/v1/projects', {
      project_name: 'Demo',
      project_id: 'demo',
      description: '',
      color: '#112233',
    })
    expect(created.id).toBe('demo')
    expect(created.isActive).toBe(true)
  })

  it('omits an empty project_id so the backend can slugify the name', async () => {
    postRequest.mockResolvedValue({
      data: { project_id: 'auto', project_name: 'Auto', is_active: 1 },
    })
    await createProject({ name: 'Auto', id: '' })
    expect(postRequest).toHaveBeenCalledWith('/api/v1/projects', {
      project_name: 'Auto',
      project_id: undefined,
      description: null,
      color: null,
    })
  })

  it('propagates create errors (e.g. duplicate name / id conflict)', async () => {
    postRequest.mockRejectedValue(new Error('duplicate'))
    extractApiErrorMessage.mockReturnValueOnce('Failed to create project.')
    await expect(createProject({ name: 'Demo' })).rejects.toThrow('Failed to create project.')
  })
})

describe('suggestProjectSlug', () => {
  it('returns the suggested slug from /api/v1/slug/romanize', async () => {
    postRequest.mockResolvedValue({ data: { suggested: 'demo-project' } })
    await expect(suggestProjectSlug('Demo Project')).resolves.toBe('demo-project')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/slug/romanize', { text: 'Demo Project' })
  })

  it('returns null for blank input without calling the API', async () => {
    await expect(suggestProjectSlug('   ')).resolves.toBeNull()
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('propagates suggestion errors', async () => {
    postRequest.mockRejectedValue(new Error('boom'))
    extractApiErrorMessage.mockReturnValueOnce('Failed to suggest a project id.')
    await expect(suggestProjectSlug('Demo')).rejects.toThrow('Failed to suggest a project id.')
  })
})
